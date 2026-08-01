import pandas as pd
from datetime import datetime, timedelta, timezone
from database.connection import get_conn

def obter_agora_fortaleza():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Fortaleza")).replace(tzinfo=None)
    except Exception:
        tz_brt = timezone(timedelta(hours=-3))
        return datetime.now(tz_brt).replace(tzinfo=None)

def obter_movimentacoes_processadas(conn):
    """
    Busca todas as movimentacoes do banco de dados e realiza o parse robusto de datas.
    Retorna um DataFrame ordenado cronologicamente.
    """
    movs = pd.read_sql("""
        SELECT id_produto, data_hora, tipo, quantidade, saldo_resultante 
        FROM movimentacoes 
        ORDER BY id ASC
    """, conn)
    
    if movs.empty:
        movs['dt'] = pd.Series(dtype='datetime64[ns]')
        return movs
        
    # Converter data_hora para datetime de forma resiliente
    movs['dt'] = pd.to_datetime(movs['data_hora'], format='%d/%m/%Y %H:%M', errors='coerce')
    mask_nat = movs['dt'].isna()
    if mask_nat.any():
        movs.loc[mask_nat, 'dt'] = pd.to_datetime(movs.loc[mask_nat, 'data_hora'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
        
    mask_nat2 = movs['dt'].isna()
    if mask_nat2.any():
        movs.loc[mask_nat2, 'dt'] = pd.to_datetime(movs.loc[mask_nat2, 'data_hora'], errors='coerce')
        
    # Ordenar cronologicamente
    movs = movs.sort_values(by='dt').reset_index(drop=True)
    return movs

def obter_saldo_em(prod_movs, dt_limite):
    """
    Retorna o saldo resultante do produto na data/hora especificada.
    Caso nao haja movimentacao anterior, calcula retroativamente a partir da primeira movimentacao.
    """
    movs_antes = prod_movs[prod_movs['dt'] <= dt_limite]
    if not movs_antes.empty:
        return int(movs_antes.iloc[-1]['saldo_resultante'])
        
    if not prod_movs.empty:
        first_mov = prod_movs.iloc[0]
        # saldo_resultante = saldo_anterior + quantidade => saldo_anterior = saldo_resultante - quantidade
        return int(first_mov['saldo_resultante'] - first_mov['quantidade'])
        
    return 0

def calcular_consumo_intervalo(prod_movs, t_start, t_end, metodo):
    """
    Calcula o consumo do produto no intervalo (t_start, t_end].
    - metodo 'inventario': Saldo Inicial + Entradas (additions) - Saldo Final
    - metodo 'movimentacoes': Soma absoluta das Saidas e Ajustes negativos
    """
    if prod_movs.empty:
        return 0
        
    if metodo == 'inventario':
        saldo_inicial = obter_saldo_em(prod_movs, t_start)
        saldo_final = obter_saldo_em(prod_movs, t_end)
        # Adicoes sao quaisquer movimentacoes com quantidade > 0 no periodo
        additions = prod_movs[
            (prod_movs['dt'] > t_start) & 
            (prod_movs['dt'] <= t_end) & 
            (prod_movs['quantidade'] > 0)
        ]['quantidade'].sum()
        
        return max(0, int(saldo_inicial + additions - saldo_final))
    else:
        # Movimentacoes: Saida ou Contagem negativa
        mask_period = (prod_movs['dt'] > t_start) & (prod_movs['dt'] <= t_end)
        mask_out = (prod_movs['tipo'] == 'Saída') | ((prod_movs['tipo'] == 'Contagem') & (prod_movs['quantidade'] < 0))
        return int(prod_movs[mask_period & mask_out]['quantidade'].abs().sum())

def obter_periodos_semanais(prod_movs, agora):
    """
    Define 4 periodos semanais (semanas S-1, S-2, S-3, S-4) baseados nos inventarios (Contagem).
    Retorna 5 timestamps (T0, T1, T2, T3, T4) definindo as janelas:
      - S-1: (T1, T0]
      - S-2: (T2, T1]
      - S-3: (T3, T2]
      - S-4: (T4, T3]
    """
    contagens = prod_movs[prod_movs['tipo'] == 'Contagem'].sort_values(by='dt', ascending=False)
    
    contagens_list = []
    last_dt = None
    for _, row in contagens.iterrows():
        curr_dt = row['dt']
        if pd.isna(curr_dt):
            continue
        if last_dt is None or (last_dt - curr_dt).total_seconds() > 3600:
            contagens_list.append(curr_dt)
            last_dt = curr_dt
            
    n_counts = len(contagens_list)
    t0 = agora
    
    if n_counts >= 4:
        t1 = contagens_list[0]
        t2 = contagens_list[1]
        t3 = contagens_list[2]
        t4 = contagens_list[3]
    elif n_counts == 3:
        t1 = contagens_list[0]
        t2 = contagens_list[1]
        t3 = contagens_list[2]
        t4 = t3 - timedelta(days=7)
    elif n_counts == 2:
        t1 = contagens_list[0]
        t2 = contagens_list[1]
        t3 = t2 - timedelta(days=7)
        t4 = t2 - timedelta(days=14)
    elif n_counts == 1:
        t1 = contagens_list[0]
        t2 = t1 - timedelta(days=7)
        t3 = t1 - timedelta(days=14)
        t4 = t1 - timedelta(days=21)
    else:
        t1 = t0 - timedelta(days=7)
        t2 = t0 - timedelta(days=14)
        t3 = t0 - timedelta(days=21)
        t4 = t0 - timedelta(days=28)
        
    return t0, t1, t2, t3, t4

def processar_consumo_produtos(df_produtos, metodo, janela_dias):
    """
    Calcula consumo_diario, consumo_s1, consumo_s2, consumo_s3, consumo_s4, consumo_4inv e tendencia
    para todos os produtos com base no metodo selecionado.
    """
    df = df_produtos.copy()
    
    # Inicializar as novas colunas
    df['consumo_diario'] = 0.0
    df['consumo_s1'] = 0
    df['consumo_s2'] = 0
    df['consumo_s3'] = 0
    df['consumo_s4'] = 0
    df['consumo_4inv'] = 0
    df['consumo_diario_4inv'] = 0.0
    df['historico_4inv_txt'] = '0 | 0 | 0 | 0'
    df['tendencia'] = '➡️ Estável'
    df['total'] = 0.0  # Para compatibilidade com outras formulas de giro de estoque
    
    try:
        with get_conn() as conn:
            movs = obter_movimentacoes_processadas(conn)
    except Exception:
        # Fallback se der erro de conexao
        return df
        
    agora = obter_agora_fortaleza()
    
    for idx, row in df.iterrows():
        prod_id = row['id']
        prod_movs = movs[movs['id_produto'] == prod_id]
        
        # 1. Calcular consumo_diario para a janela de dias selecionada
        t_limite = agora - timedelta(days=janela_dias)
        consumo_total_janela = calcular_consumo_intervalo(prod_movs, t_limite, agora, metodo)
        df.at[idx, 'total'] = float(consumo_total_janela)
        df.at[idx, 'consumo_diario'] = float(consumo_total_janela) / (janela_dias if janela_dias > 0 else 1)
        
        # 2. Calcular consumos das últimas 4 semanas de inventário (S-1, S-2, S-3, S-4)
        t0, t1, t2, t3, t4 = obter_periodos_semanais(prod_movs, agora)
        
        s1 = calcular_consumo_intervalo(prod_movs, t1, t0, metodo)
        s2 = calcular_consumo_intervalo(prod_movs, t2, t1, metodo)
        s3 = calcular_consumo_intervalo(prod_movs, t3, t2, metodo)
        s4 = calcular_consumo_intervalo(prod_movs, t4, t3, metodo)
        
        df.at[idx, 'consumo_s1'] = s1
        df.at[idx, 'consumo_s2'] = s2
        df.at[idx, 'consumo_s3'] = s3
        df.at[idx, 'consumo_s4'] = s4
        
        tot_4inv = s1 + s2 + s3 + s4
        df.at[idx, 'consumo_4inv'] = tot_4inv
        df.at[idx, 'consumo_diario_4inv'] = float(tot_4inv) / 28.0
        df.at[idx, 'historico_4inv_txt'] = f"{s1} | {s2} | {s3} | {s4}"
        
        # 3. Calcular tendencia baseada na diferenca entre a semana mais recente (s1) e a anterior (s2)
        diff = s1 - s2
        if diff > 0:
            df.at[idx, 'tendencia'] = f"📈 Aumento (+{diff})"
        elif diff < 0:
            df.at[idx, 'tendencia'] = f"📉 Queda ({diff})"
        else:
            df.at[idx, 'tendencia'] = "➡️ Estável"
            
    return df

def calcular_previsao_demanda_preditiva(df_produtos, metodo='movimentacoes', janela_dias=30):
    """
    Realiza a projecao preditiva de demanda baseada nos 4 últimos consumos de inventário
    para os proximos 30, 60 e 90 dias.
    Calcula tambem o Runway (dias ate esgotamento) e a data estimada de ruptura.
    """
    df = processar_consumo_produtos(df_produtos, metodo, janela_dias)
    agora = obter_agora_fortaleza()
    
    # Adicionar colunas preditivas
    df['prev_30d'] = 0.0
    df['prev_60d'] = 0.0
    df['prev_90d'] = 0.0
    df['runway_dias'] = 999.0
    df['data_esgotamento'] = 'Indeterminado (Sem consumo)'
    df['giro_anual'] = 0.0
    df['status_cobertura'] = '🟢 Saudável'
    
    for idx, row in df.iterrows():
        consumo_diario = row.get('consumo_diario', 0.0)
        s1 = row.get('consumo_s1', 0)
        s2 = row.get('consumo_s2', 0)
        s3 = row.get('consumo_s3', 0)
        s4 = row.get('consumo_s4', 0)
        saldo = row.get('saldo_atual', 0)
        
        # Consumo diario ponderado (S-1: 40%, S-2: 30%, S-3: 20%, S-4: 10%)
        s_total = s1 + s2 + s3 + s4
        if s_total > 0:
            consumo_semanal_ponderado = (s1 * 0.40) + (s2 * 0.30) + (s3 * 0.20) + (s4 * 0.10)
            c_diario_ponderado = max(consumo_diario, consumo_semanal_ponderado / 7.0)
        else:
            c_diario_ponderado = consumo_diario
            
        prev_30 = c_diario_ponderado * 30
        prev_60 = c_diario_ponderado * 60
        prev_90 = c_diario_ponderado * 90
        
        df.at[idx, 'prev_30d'] = round(prev_30, 1)
        df.at[idx, 'prev_60d'] = round(prev_60, 1)
        df.at[idx, 'prev_90d'] = round(prev_90, 1)
        
        # Giro anualizado (Consumo em 365 dias / Estoque Médio)
        if saldo > 0:
            df.at[idx, 'giro_anual'] = round((c_diario_ponderado * 365.0) / float(saldo), 2)
        else:
            df.at[idx, 'giro_anual'] = 0.0
            
        # Runway e Data de esgotamento
        if c_diario_ponderado > 0:
            runway = float(saldo) / c_diario_ponderado
            df.at[idx, 'runway_dias'] = round(runway, 1)
            data_ruptura = agora + timedelta(days=int(runway))
            df.at[idx, 'data_esgotamento'] = data_ruptura.strftime('%d/%m/%Y')
            
            if runway <= 7:
                df.at[idx, 'status_cobertura'] = '🔴 Risco Crítico (< 7 dias)'
            elif runway <= 15:
                df.at[idx, 'status_cobertura'] = '🟠 Alerta de Pedido (7-15 dias)'
            elif runway <= 30:
                df.at[idx, 'status_cobertura'] = '🟡 Cobertura Curta (15-30 dias)'
            else:
                df.at[idx, 'status_cobertura'] = '🟢 Cobertura Confortável (> 30 dias)'
        else:
            df.at[idx, 'runway_dias'] = 999.0
            df.at[idx, 'data_esgotamento'] = 'N/A (Sem Saídas)'
            if saldo > 0:
                df.at[idx, 'status_cobertura'] = '⚪ Parado sem Giro'
            else:
                df.at[idx, 'status_cobertura'] = '🔴 Estoque Zerado'
                
    return df

def calcular_custo_posse_estoque(df_produtos, taxa_anual_posse=0.15):
    """
    Calcula as metricas de Custo de Posse/Carregamento do Estoque (Carrying Cost)
    e identifica capital imobilizado parado (sem giro).
    Taxa padrao de posse: 15% ao ano (~1.25% ao mes).
    """
    df = df_produtos.copy()
    if 'valor_unitario' not in df.columns or 'saldo_atual' not in df.columns:
        return {}
        
    df['valor_total'] = df['saldo_atual'] * df['valor_unitario']
    valuation_total = float(df['valor_total'].sum())
    
    # Custo de Posse Mensal e Anual
    custo_posse_anual = valuation_total * taxa_anual_posse
    custo_posse_mensal = custo_posse_anual / 12.0
    
    # Identificar itens sem giro (Consumo diário zero ou muito baixo e saldo > 0)
    consumo_col = 'consumo_diario' if 'consumo_diario' in df.columns else None
    if consumo_col:
        mask_parado = (df['saldo_atual'] > 0) & (df[consumo_col] == 0)
    else:
        mask_parado = (df['saldo_atual'] > 0)
        
    df_parados = df[mask_parado].copy()
    valor_parado = float(df_parados['valor_total'].sum()) if not df_parados.empty else 0.0
    pct_capital_parado = (valor_parado / valuation_total * 100.0) if valuation_total > 0 else 0.0
    
    # Custo de oportunidade mensal do capital parado
    custo_parado_mensal = valor_parado * (taxa_anual_posse / 12.0)
    
    return {
        'valuation_total': valuation_total,
        'custo_posse_anual': custo_posse_anual,
        'custo_posse_mensal': custo_posse_mensal,
        'valor_capital_parado': valor_parado,
        'pct_capital_parado': round(pct_capital_parado, 1),
        'custo_parado_mensal': custo_parado_mensal,
        'taxa_anual_posse': taxa_anual_posse,
        'total_itens_parados': len(df_parados),
        'df_parados': df_parados
    }

def calcular_matriz_kraljic(df_produtos):
    """
    Classifica cada insumo nos 4 quadrantes da Matriz Kraljic:
      1. 🔴 Estratégico: Alto Impacto Financeiro (Classe A) + Alto Risco de Suprimento (Criticidade Z ou Lead Time >= 5d)
      2. 🟠 Gargalo: Baixo Impacto Financeiro (Classe B/C) + Alto Risco de Suprimento (Criticidade Z ou Lead Time >= 5d)
      3. 🟡 Alavancagem: Alto Impacto Financeiro (Classe A) + Baixo Risco de Suprimento (Criticidade X/Y e Lead Time < 5d)
      4. 🟢 Rotineiro: Baixo Impacto Financeiro (Classe B/C) + Baixo Risco de Suprimento (Criticidade X/Y e Lead Time < 5d)
    """
    df = df_produtos.copy()
    if df.empty:
        df['Quadrante_Kraljic'] = []
        df['Estrategia_Recomendada'] = []
        return df

    if 'Classe_ABC' not in df.columns:
        df['valor_total'] = df['saldo_atual'] * df['valor_unitario']
        df_sorted = df.sort_values(by='valor_total', ascending=False).copy()
        tot_val = df_sorted['valor_total'].sum()
        if tot_val > 0:
            df_sorted['perc_acum'] = (df_sorted['valor_total'].cumsum() / tot_val) * 100
            df_sorted['Classe_ABC'] = df_sorted['perc_acum'].apply(lambda p: 'A' if p <= 80 else ('B' if p <= 95 else 'C'))
        else:
            df_sorted['Classe_ABC'] = 'C'
        df['Classe_ABC'] = df['id'].map(dict(zip(df_sorted['id'], df_sorted['Classe_ABC']))).fillna('C')

    def classificar_kraljic(row):
        abc = str(row.get('Classe_ABC', 'C')).upper()
        xyz = str(row.get('criticidade', 'Y')).upper()
        lead = int(row.get('lead_time', 3))

        alto_impacto = (abc == 'A')
        alto_risco = (xyz == 'Z') or (lead >= 5)

        if alto_impacto and alto_risco:
            return "🔴 Estratégico", "Contratos de longo prazo, alianças com fornecedores e estoques de segurança rigorosos"
        elif not alto_impacto and alto_risco:
            return "🟠 Gargalo", "Garantia de suprimento, busca ativa de fornecedores substitutos e estoques de contingência"
        elif alto_impacto and not alto_risco:
            return "🟡 Alavancagem", "Cotações agressivas de preço, negociação por volume e contratos curtos para obter margem"
        else:
            return "🟢 Rotineiro", "Padronização de pedidos, automação de compras e baixas operacionais enxutas"

    results = df.apply(classificar_kraljic, axis=1)
    df['Quadrante_Kraljic'] = [r[0] for r in results]
    df['Estrategia_Recomendada'] = [r[1] for r in results]
    return df

def obter_historico_precos_insumos(conn):
    """
    Busca o historico de compras/entradas no banco de dados e extrai a evolucao
    do preco unitario pago por cada insumo ao longo do tempo.
    """
    import re
    movs = pd.read_sql("""
        SELECT m.id_produto, p.nome AS produto, p.categoria, m.data_hora, m.quantidade, m.observacao, p.valor_unitario AS preco_atual
        FROM movimentacoes m
        JOIN produtos p ON p.id = m.id_produto
        WHERE m.tipo = 'Entrada'
        ORDER BY m.id ASC
    """, conn)
    
    if movs.empty:
        return pd.DataFrame(columns=['id_produto', 'produto', 'categoria', 'dt', 'data_hora', 'preco_pago'])
        
    movs['dt'] = pd.to_datetime(movs['data_hora'], format='%d/%m/%Y %H:%M', errors='coerce')
    mask_nat = movs['dt'].isna()
    if mask_nat.any():
        movs.loc[mask_nat, 'dt'] = pd.to_datetime(movs.loc[mask_nat, 'data_hora'], errors='coerce')
        
    precos = []
    for _, row in movs.iterrows():
        obs = str(row['observacao'])
        match = re.search(r'Pago:\s*R\$\s*([\d\.,]+)', obs)
        if match:
            try:
                p_str = match.group(1).replace('.', '').replace(',', '.')
                precos.append(float(p_str))
            except Exception:
                precos.append(float(row['preco_atual']))
        else:
            precos.append(float(row['preco_atual']))
            
    movs['preco_pago'] = precos
    return movs.sort_values(by='dt').reset_index(drop=True)

