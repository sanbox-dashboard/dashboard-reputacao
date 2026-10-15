#!/usr/bin/env python3
"""Atualização diária da aba "Problemas x Vendas" (dashboard de Reputação).

Gera/atualiza probvendas.json na raiz do repositório com o que veio DEPOIS da
base fixa (que termina em 30/09/2026 e fica embutida no index.html):

- tickets Octadesk a partir de 01/10/2026 (planilha OctaDesk-Async, aba Tickets):
  data = coluna L, problema = coluna AK, SKU = coluna AQ, pedido = coluna AF.
  Os tickets ficam guardados no próprio JSON (chave = nº do ticket), então nada
  se perde se o ticket sair da planilha; se o ticket mudar, vale a versão mais nova.
- vendas a partir de 01/10/2026 (KONDADO, aba tinyerp_pedidos_detalhes_itens):
  SKU = B, quantidade = E, situação = J, data do pedido = L (M/D/AAAA).
  Só Aprovado, Preparando envio, Faturado, Pronto para envio, Enviado e Entregue.
  ⚠️ A planilha de pedidos apaga dados com mais de ~3 meses. Por isso as vendas
  ficam guardadas MÊS A MÊS em probvendas.json ("vendasMes"): só o mês atual e o
  anterior são recalculados a cada execução; meses mais antigos ficam CONGELADOS
  (nunca mais são sobrescritos) e ganham uma cópia em historico/vendas-AAAA-MM.json.
- marca (tinyerp_produtos, coluna Y) e estoque (tinyerp_estoque_produto, coluna C),
  fotografados uma vez por execução (a tarefa roda de madrugada).

Regras de contagem iguais às da base fixa:
- RMA = "RMA"; Insatisfação = "Insatisfação Compra";
  Danificado = Danificado Transporte Marketplace + Danificado Transporte Próprio
  + Recusa por Avaria da Transportadora.
- SKU com "(N unidades)" conta N; só o SKU conta 1; vários SKUs na mesma linha
  contam cada um.
- Cada problema conta 1x por pedido + SKU.

Uso: python3 scripts/probvendas_update.py [caminho/probvendas.json]
"""
import json, os, re, sys, unicodedata, urllib.request, urllib.parse
from datetime import date, datetime
from zoneinfo import ZoneInfo

KEY = 'AIzaSyCpFaibN_ct8r2ywjIlKByvZkxok5ytqXI'  # chave pública já usada pelo dashboard
KONDADO = '1d4ORz_TloSJPBq8LZMrbD7YoPZSUFONExKg1heEBFrU'
OCTA = '16nUZLpa4miVkz1asgCs8FoC8VPFP5VDlWmWRLgaR-Aw'
DESDE = date(2026, 10, 1)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'probvendas.json')

CATS = {
    'rma': 'rma',
    'insatisfacao compra': 'insat',
    'danificado transporte marketplace': 'dano',
    'danificado transporte proprio': 'dano',
    'recusa por avaria da transportadora': 'dano',
}
SITUACOES_OK = {'aprovado', 'preparando envio', 'faturado', 'pronto para envio', 'enviado', 'entregue'}
TOK = re.compile(r'([A-Z]{2,3}\d+(?:OUT)?(?:\s*-\s*[A-Z0-9]+)?)\s*(?:\(\s*0*(\d+)\s*[A-Z]*)?', re.I)


def norm(s):
    s = unicodedata.normalize('NFD', (s or '').strip().lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def get(sheet_id, rng):
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{urllib.parse.quote(rng)}?key={KEY}'
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r).get('values', [])


def cell(row, i):
    return row[i].strip() if len(row) > i and row[i] is not None else ''


def data_br(s):  # DD/MM/AAAA [hh:mm]
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s or '')
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def data_us(s):  # M/D/AAAA
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s or '')
    return date(int(m[3]), int(m[1]), int(m[2])) if m else None


def parse_skus(raw):
    s = (raw or '').upper()
    out = []
    for m in TOK.finditer(s):
        code = re.sub(r'\s+', '', m.group(1)).rstrip('-')
        out.append([code, int(m.group(2)) if m.group(2) else 1])
    if not out and re.fullmatch(r'\d+', s.strip()):
        out = [[s.strip(), 1]]
    return out


def main():
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
    except FileNotFoundError:
        prev = {}
    tickets = dict(prev.get('tickets', {}))  # acumulado permanente (a planilha pode perder tickets)

    # ---- Tickets Octadesk (A=nº, L=data, AF=pedido, AK=problema, AQ=SKU) ----
    t = get(OCTA, 'Tickets!A:AQ')
    lidos = novos = 0
    for r in t[1:]:
        num = cell(r, 0)
        d = data_br(cell(r, 11))
        if not num or not d or d < DESDE:
            continue
        lidos += 1
        cat = CATS.get(norm(cell(r, 36)))
        if not cat:
            tickets.pop(num, None)  # problema mudou para um que não conta
            continue
        if num not in tickets:
            novos += 1
        tickets[num] = [d.isoformat(), cell(r, 31) or ('ticket-' + num), cat, parse_skus(cell(r, 42))]

    # dedupe: 1x por pedido + categoria + SKU (unidades = maior valor informado)
    ded = {}
    for num, (d, pedido, cat, skus) in tickets.items():
        for sku, q in skus:
            k = (pedido, cat, sku)
            ded[k] = max(ded.get(k, 0), q)
    prob = {}
    for (pedido, cat, sku), q in ded.items():
        p = prob.setdefault(sku, [0, 0, 0])
        p[['rma', 'insat', 'dano'].index(cat)] += q
    sem_sku = sum(1 for v in tickets.values() if not v[3])

    # ---- Vendas KONDADO itens (B=SKU, C=descrição, E=qtd, J=situação, L=data) ----
    it = get(KONDADO, 'tinyerp_pedidos_detalhes_itens!A:L')
    hoje = date.fromisoformat(os.environ['PV_HOJE']) if os.environ.get('PV_HOJE') else datetime.now(ZoneInfo('America/Sao_Paulo')).date()  # PV_HOJE só para teste
    m_ant = (hoje.replace(day=1) - __import__('datetime').timedelta(days=1)).strftime('%Y-%m')
    VIVOS = {hoje.strftime('%Y-%m'), m_ant}       # recalculados; o resto fica congelado
    vendas_mes = {m: dict(v) for m, v in prev.get('vendasMes', {}).items()}
    congelados = set(prev.get('mesesCongelados', []))
    novo_mes, nomes_venda = {}, {}
    for r in it[1:]:
        if norm(cell(r, 9)) not in SITUACOES_OK:
            continue
        d = data_us(cell(r, 11))
        if not d or d < DESDE:
            continue
        sku = cell(r, 1).upper().replace(' ', '')
        if not sku:
            continue
        try:
            q = float(cell(r, 4).replace(',', '.') or 0)
        except ValueError:
            q = 0
        ym = d.strftime('%Y-%m')
        if ym not in VIVOS:
            continue                              # mês congelado: nunca recalcula
        mv = novo_mes.setdefault(ym, {})
        mv[sku] = mv.get(sku, 0) + q
        nomes_venda.setdefault(sku, cell(r, 2))
    for ym in VIVOS:
        if ym >= DESDE.strftime('%Y-%m'):
            vendas_mes[ym] = {k: int(v) if float(v).is_integer() else round(v, 2) for k, v in sorted(novo_mes.get(ym, {}).items())}
    # meses que saíram da janela viva: congela e salva cópia histórica
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(OUT)), 'historico'), exist_ok=True)
    for ym in sorted(vendas_mes):
        if ym not in VIVOS and ym not in congelados:
            congelados.add(ym)
            with open(os.path.join(os.path.dirname(os.path.abspath(OUT)), 'historico', f'vendas-{ym}.json'), 'w', encoding='utf-8') as f:
                json.dump({'mes': ym, 'congeladoEm': hoje.isoformat(), 'vendas': vendas_mes[ym]}, f, ensure_ascii=False, separators=(',', ':'))
            print(f'Mês {ym} congelado e salvo em historico/vendas-{ym}.json')
    vendas = {}
    for mv in vendas_mes.values():
        for k, v in mv.items():
            vendas[k] = vendas.get(k, 0) + v

    # ---- Marca (produtos H=código, Y=marca, AA=nome) e estoque (B=código, C=saldo) ----
    marcas, nomes = {}, {}
    for r in get(KONDADO, 'tinyerp_produtos!H:AA')[1:]:
        sku = cell(r, 0).upper()
        if sku:
            marcas[sku] = cell(r, 17)
            nomes[sku] = cell(r, 19)
    estoque = {}
    for r in get(KONDADO, 'tinyerp_estoque_produto!A:C')[1:]:
        sku = cell(r, 1).upper()
        if sku:
            try:
                estoque[sku] = estoque.get(sku, 0) + float(cell(r, 2).replace(',', '.') or 0)
            except ValueError:
                pass
    estoque = {k: int(v) if float(v).is_integer() else v for k, v in estoque.items()}

    # nome só para SKUs novos (os da base fixa já têm nome)
    nomes_inc = {s: (nomes.get(s) or nomes_venda.get(s) or '') for s in set(vendas) | set(prob)}

    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    out = {
        'versao': 1,
        'desde': DESDE.isoformat(),
        'updatedAt': agora.isoformat(timespec='seconds'),
        'fontes': {
            'tickets': f'OctaDesk-Async/Tickets ({OCTA})',
            'vendas': f'KONDADO/tinyerp_pedidos_detalhes_itens ({KONDADO})',
            'marca': 'KONDADO/tinyerp_produtos coluna Y',
            'estoque': 'KONDADO/tinyerp_estoque_produto coluna C (foto da madrugada)',
        },
        'vendas': dict(sorted(vendas.items())),       # soma de todos os meses desde 01/10/2026
        'vendasMes': dict(sorted(vendas_mes.items())),
        'mesesCongelados': sorted(congelados),
        'problemas': dict(sorted(prob.items())),
        'nomes': dict(sorted(nomes_inc.items())),
        'marcas': dict(sorted((k, v) for k, v in marcas.items() if v)),
        'estoque': dict(sorted(estoque.items())),
        'tickets': dict(sorted(tickets.items())),
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    tot = [sum(v[i] for v in prob.values()) for i in range(3)]
    print(f'OK {agora:%d/%m/%Y %H:%M} · tickets lidos desde {DESDE:%d/%m}: {lidos} (novos {novos}, que contam {len(tickets)}, sem SKU {sem_sku}) · '
          f'RMA {tot[0]} · Insat {tot[1]} · Danif {tot[2]} · unidades vendidas {sum(vendas.values())} · '
          f'marcas {len(out["marcas"])} · estoque {len(estoque)} SKUs')


if __name__ == '__main__':
    main()
