import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from database.connection import get_conn

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
    Define 3 periodos semanais (semanas S-1, S-2, S-3) baseados nos inventarios (Contagem).
    Retorna 4 timestamps (T0, T1, T2, T3) definindo as janelas:
      - S-1: (T1, T0]
      - S-2: (T2, T1]
      - S-3: (T3, T2]
    """
    # Obter contagens do produto, ordenadas decrescentemente no tempo
    contagens = prod_movs[prod_movs['tipo'] == 'Contagem'].sort_values(by='dt', ascending=False)
    
    contagens_list = []
    last_dt = None
    for _, row in contagens.iterrows():
        curr_dt = row['dt']
        if pd.isna(curr_dt):
            continue
        # Evitar contagens duplicadas no mesmo dia/hora (considera intervalo > 1 hora)
        if last_dt is None or (last_dt - curr_dt).total_seconds() > 3600:
            contagens_list.append(curr_dt)
            last_dt = curr_dt
            
    n_counts = len(contagens_list)
    t0 = agora
    
    if n_counts >= 3:
        # Se temos pelo menos 3 contagens no historico, usamos as contagens como marcos
        t1 = contagens_list[0]
        t2 = contagens_list[1]
        t3 = contagens_list[2]
    elif n_counts == 2:
        t1 = contagens_list[0]
        t2 = contagens_list[1]
        t3 = t2 - timedelta(days=7)
    elif n_counts == 1:
        t1 = contagens_list[0]
        t2 = t1 - timedelta(days=7)
        t3 = t1 - timedelta(days=14)
    else:
        # Sem contagens: janelas calendarias puras de 7 dias
        t1 = t0 - timedelta(days=7)
        t2 = t0 - timedelta(days=14)
        t3 = t0 - timedelta(days=21)
        
    return t0, t1, t2, t3

def processar_consumo_produtos(df_produtos, metodo, janela_dias):
    """
    Calcula consumo_diario, consumo_s1, consumo_s2, consumo_s3 e tendencia
    para todos os produtos com base no metodo selecionado.
    """
    df = df_produtos.copy()
    
    # Inicializar as novas colunas
    df['consumo_diario'] = 0.0
    df['consumo_s1'] = 0
    df['consumo_s2'] = 0
    df['consumo_s3'] = 0
    df['tendencia'] = '➡️ Estável'
    df['total'] = 0.0  # Para compatibilidade com outras formulas de giro de estoque
    
    try:
        with get_conn() as conn:
            movs = obter_movimentacoes_processadas(conn)
    except Exception:
        # Fallback se der erro de conexao
        return df
        
    agora = datetime.now(ZoneInfo("America/Fortaleza")).replace(tzinfo=None)
    
    for idx, row in df.iterrows():
        prod_id = row['id']
        prod_movs = movs[movs['id_produto'] == prod_id]
        
        # 1. Calcular consumo_diario para a janela de dias selecionada
        t_limite = agora - timedelta(days=janela_dias)
        consumo_total_janela = calcular_consumo_intervalo(prod_movs, t_limite, agora, metodo)
        df.at[idx, 'total'] = float(consumo_total_janela)
        df.at[idx, 'consumo_diario'] = float(consumo_total_janela) / janela_dias
        
        # 2. Calcular consumos semanais S-1, S-2, S-3
        t0, t1, t2, t3 = obter_periodos_semanais(prod_movs, agora)
        
        s1 = calcular_consumo_intervalo(prod_movs, t1, t0, metodo)
        s2 = calcular_consumo_intervalo(prod_movs, t2, t1, metodo)
        s3 = calcular_consumo_intervalo(prod_movs, t3, t2, metodo)
        
        df.at[idx, 'consumo_s1'] = s1
        df.at[idx, 'consumo_s2'] = s2
        df.at[idx, 'consumo_s3'] = s3
        
        # 3. Calcular tendencia baseada na diferenca entre a semana mais recente (s1) e a anterior (s2)
        diff = s1 - s2
        if diff > 0:
            df.at[idx, 'tendencia'] = f"📈 Aumento (+{diff})"
        elif diff < 0:
            df.at[idx, 'tendencia'] = f"📉 Queda ({diff})"
        else:
            df.at[idx, 'tendencia'] = "➡️ Estável"
            
    return df
