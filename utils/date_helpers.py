import datetime
from database.connection import get_conn

def obter_parametros_cronograma():
    params = {
        "dias_antes_inicio_sol": 5,
        "dias_antes_fim_sol": 3,
        "dias_uteis_analise": 5,
        "dias_uteis_entrega": 3
    }
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT chave, valor FROM configuracoes WHERE chave LIKE 'crono_%'").fetchall()
            for key, val in rows:
                k = key.replace("crono_", "")
                params[k] = int(val)
    except Exception:
        pass
    return params

def obter_ultimo_dia_mes(dt):
    """Retorna o último dia do mês para a data fornecida."""
    if dt.month == 12:
        return datetime.date(dt.year, 12, 31)
    return datetime.date(dt.year, dt.month + 1, 1) - datetime.timedelta(days=1)

def obter_primeiro_dia_util(ano, mes):
    """Retorna o primeiro dia útil do mês especificado (excluindo sábados e domingos)."""
    dt = datetime.date(ano, mes, 1)
    while dt.weekday() >= 5:  # 5 = Sábado, 6 = Domingo
        dt += datetime.timedelta(days=1)
    return dt

def adicionar_dias_uteis(data_inicial, dias):
    """Adiciona N dias úteis a uma data inicial (excluindo sábados e domingos)."""
    dt = data_inicial
    dias_adicionados = 0
    while dias_adicionados < dias:
        dt += datetime.timedelta(days=1)
        if dt.weekday() < 5:
            dias_adicionados += 1
    return dt

def obter_cronograma_mes(ano, mes):
    """
    Calcula as datas importantes para o ciclo de compras de um mês alvo específico
    utilizando os parâmetros operacionais dinâmicos carregados do banco de dados.
    """
    params = obter_parametros_cronograma()
    d_inicio = params["dias_antes_inicio_sol"]
    d_fim = params["dias_antes_fim_sol"]
    d_analise = params["dias_uteis_analise"]
    d_entrega = params["dias_uteis_entrega"]

    # Determinar o mês anterior para a janela de solicitação
    if mes == 1:
        ano_anterior = ano - 1
        mes_anterior = 12
    else:
        ano_anterior = ano
        mes_anterior = mes - 1
        
    ultimo_dia_anterior = obter_ultimo_dia_mes(datetime.date(ano_anterior, mes_anterior, 1))
    
    # Janela de solicitação
    data_inicio_solicitacao = ultimo_dia_anterior - datetime.timedelta(days=d_inicio)
    data_fim_solicitacao = ultimo_dia_anterior - datetime.timedelta(days=d_fim)
    
    # Verifica se há override específico para este ciclo
    key_override = f"crono_override_sol_{ano}_{mes}"
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = ?", (key_override,)).fetchone()
            if row:
                parts = row[0].split(":")
                if len(parts) == 2:
                    data_inicio_solicitacao = datetime.date.fromisoformat(parts[0])
                    data_fim_solicitacao = datetime.date.fromisoformat(parts[1])
    except Exception:
        pass
    
    # Início da análise: 1º dia útil do mês alvo
    data_inicio_analise = obter_primeiro_dia_util(ano, mes)
    
    # Aprovação (Lead Time interno)
    data_aprovacao = adicionar_dias_uteis(data_inicio_analise, d_analise)
    
    # Entrega (Lead Time fornecedor)
    data_entrega = adicionar_dias_uteis(data_aprovacao, d_entrega)
    
    return {
        "mes_alvo": mes,
        "ano_alvo": ano,
        "inicio_solicitacao": data_inicio_solicitacao,
        "fim_solicitacao": data_fim_solicitacao,
        "inicio_analise": data_inicio_analise,
        "data_aprovacao": data_aprovacao,
        "data_entrega": data_entrega
    }

def calcular_previsao_entrega(hoje=None):
    """
    Com base na data de hoje, determina qual o próximo ciclo de entrega aplicável:
    - Se hoje <= data fim de solicitação do mês seguinte, participa desse ciclo.
    - Caso contrário, vai para o ciclo do outro mês.
    Retorna o cronograma completo do ciclo aplicável.
    """
    if hoje is None:
        hoje = datetime.date.today()
        
    # Vamos checar o ciclo do próximo mês (M+1)
    if hoje.month == 12:
        ano_ciclo = hoje.year + 1
        mes_ciclo = 1
    else:
        ano_ciclo = hoje.year
        mes_ciclo = hoje.month + 1
        
    crono = obter_cronograma_mes(ano_ciclo, mes_ciclo)
    
    # Se já passou da data limite de solicitação para o mês seguinte, vai para o mês subsequente (M+2)
    if hoje > crono["fim_solicitacao"]:
        if mes_ciclo == 12:
            ano_ciclo += 1
            mes_ciclo = 1
        else:
            mes_ciclo += 1
        crono = obter_cronograma_mes(ano_ciclo, mes_ciclo)
        
    return crono
