import streamlit as st
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from io import BytesIO
import os
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
import numpy as np
import threading
import hashlib
import uuid

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA E CSS RESPONSIVO SEGURO
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="WMS 4.0 - Alta Performance", page_icon="📦", layout="wide")

st.markdown("""
    <style>
    /* Alinha os botões principais */
    .stButton>button {
        border-radius: 10px;
        font-weight: 600;
        height: 3em;
        width: 100%;
        margin-top: 10px;
    }
    /* Base dos cartões de métricas */
    .metric-card {
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 15px;
    }
    .stNumberInput, .stTextInput, .stSelectbox {
        margin-bottom: 10px;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ─── VARIÁVEIS GLOBAIS ───
DB_PATH = "estoque.db"
FOLDER_ID = st.secrets["FOLDER_ID"]

# ─────────────────────────────────────────────────────────────
# FUNÇÕES DE SEGURANÇA E CONEXÃO
# ─────────────────────────────────────────────────────────────
def get_conn(): return sqlite3.connect(DB_PATH, check_same_thread=False)

def gerar_hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

# ─────────────────────────────────────────────────────────────
# OPTIMIZAÇÃO 1: SINCRONIZAÇÃO EM SEGUNDO PLANO
# ─────────────────────────────────────────────────────────────
def executar_sincronizacao_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false"
        files = servico.files().list(q=query, fields="files(id)").execute().get('files', [])
        media = MediaFileUpload(DB_PATH, mimetype='application/x-sqlite3', resumable=True)
        if files: 
            servico.files().update(fileId=files[0]['id'], media_body=media).execute()
        else: 
            servico.files().create(body={'name': DB_PATH, 'parents': [FOLDER_ID]}, media_body=media).execute()
        
        with sqlite3.connect(DB_PATH) as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
            """, conn)
            
        for df, name in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            q = f"name='{name}' and '{FOLDER_ID}' in parents"
            fs = servico.files().list(q=q).execute().get('files', [])
            m = MediaIoBaseUpload(BytesIO(df.to_csv(index=False).encode('utf-8-sig')), mimetype='text/csv')
            if fs: servico.files().update(fileId=fs[0]['id'], media_body=m).execute()
            else: servico.files().create(body={'name': name, 'parents': [FOLDER_ID]}, media_body=m).execute()
    except:
        pass

def disparar_sincronizacao():
    st.cache_data.clear()
    threading.Thread(target=executar_sincronizacao_drive).start()

def obter_servico_drive():
    info_chaves = dict(st.secrets["gcp_service_account"])
    credenciais = service_account.Credentials.from_service_account_info(info_chaves)
    return build('drive', 'v3', credentials=credenciais)

def descarregar_do_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{DB_PATH}' and '{FOLDER_ID}' in parents and trashed=false"
        res = servico.files().list(q=query, fields="files(id)").execute()
        if res.get('files', []):
            req = servico.files().get_media(fileId=res['files'][0]['id'])
            with open(DB_PATH, "wb") as f:
                load = MediaIoBaseDownload(f, req)
                done = False
                while not done: _, done = load.next_chunk()
            return True
    except: return False
    return False

# ─────────────────────────────────────────────────────────────
# LISTAGEM EM CACHE
# ─────────────────────────────────────────────────────────────
@st.cache_data
def listar_produtos():
    with get_conn() as conn: return pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)

@st.cache_data
def listar_movimentacoes():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
            FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
        """, conn)

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                data_criacao TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                senha_hash TEXT NOT NULL,
                pergunta_seguranca TEXT NOT NULL,
                resposta_seguranca_hash TEXT NOT NULL,
                aprovado INTEGER DEFAULT 0,
                perfil TEXT DEFAULT 'Operador'
            );
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                saldo_atual INTEGER NOT NULL DEFAULT 0,
                estoque_minimo INTEGER DEFAULT 10,
                valor_unitario REAL DEFAULT 0,
                categoria TEXT DEFAULT 'Geral',
                lead_time INTEGER DEFAULT 3
            );
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto INTEGER NOT NULL REFERENCES produtos(id),
                data_hora TEXT NOT NULL,
                tipo TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                saldo_resultante INTEGER NOT NULL,
                observacao TEXT
            );
        """)
        
        # Migrações seguras
        try: conn.execute("ALTER TABLE usuarios ADD COLUMN aprovado INTEGER DEFAULT 0")
        except: pass
        try: conn.execute("ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'Operador'")
        except: pass

def cadastrar_produto(nome, estoque_minimo, valor_unitario, categoria, lead_time):
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time) VALUES (?, 0, ?, ?, ?, ?)", (nome, estoque_minimo, valor_unitario, categoria, lead_time))
        return True, "Sucesso"
    except: return False, "Erro"

def editar_produto(id_p, nome, min_e, valor, cat, lead):
    with get_conn() as conn:
        conn.execute("UPDATE produtos SET nome=?, estoque_minimo=?, valor_unitario=?, categoria=?, lead_time=? WHERE id=?", (nome, min_e, valor, cat, lead, id_p))

def deletar_produto(id_produto):
    with get_conn() as conn:
        conn.execute("DELETE FROM movimentacoes WHERE id_produto = ?", (id_produto,))
        conn.execute("DELETE FROM produtos WHERE id = ?", (id_produto,))

# ─────────────────────────────────────────────────────────────
# INICIALIZAÇÃO E CONTROLE DE SESSÃO (CORREÇÃO DO F5)
# ─────────────────────────────────────────────────────────────
if "db_sincronizado" not in st.session_state:
    descarregar_do_drive()
    init_db()
    st.session_state["db_sincronizado"] = True

# Validador de Token (Mantém logado mesmo dando F5)
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False
    st.session_state["usuario_atual"] = ""
    st.session_state["perfil_atual"] = ""
    
    # Se existe um token na URL, valida no banco de dados invisivelmente
    if "token" in st.query_params:
        token = st.query_params["token"]
        with get_conn() as conn:
            sessao = conn.execute("SELECT s.usuario, u.perfil FROM sessoes s JOIN usuarios u ON s.usuario = u.usuario WHERE s.token = ?", (token,)).fetchone()
            if sessao:
                st.session_state["autenticado"] = True
                st.session_state["usuario_atual"] = sessao[0]
                st.session_state["perfil_atual"] = sessao[1]

# ─────────────────────────────────────────────────────────────
# FLUXO DE LOGIN / ACESSO DO USUÁRIO
# ─────────────────────────────────────────────────────────────
if not st.session_state["autenticado"]:
    st.title("🔒 WMS Inteligente - Controle de Acesso")
    st.caption("Autenticação obrigatória para acesso à base operacional.")
    
    aba_login, aba_cadastro, aba_recuperar = st.tabs(["🔑 Entrar no Sistema", "👤 Criar Conta", "🛠️ Esqueci a Senha"])
    
    with aba_login:
        with st.form("form_login"):
            usr_input = st.text_input("Usuário (Login)").strip()
            pass_input = st.text_input("Senha", type="password")
            btn_login = st.form_submit_button("Acessar WMS")
            
            if btn_login:
                if usr_input and pass_input:
                    hash_login = gerar_hash_senha(pass_input)
                    with get_conn() as conn:
                        res = conn.execute("SELECT aprovado, perfil FROM usuarios WHERE usuario = ? AND senha_hash = ?", (usr_input, hash_login)).fetchone()
                    
                    if res:
                        if res[0] == 1:
                            st.session_state["autenticado"] = True
                            st.session_state["usuario_atual"] = usr_input
                            st.session_state["perfil_atual"] = res[1]
                            
                            # Gera um token seguro para salvar a sessão contra F5
                            novo_token = str(uuid.uuid4())
                            st.query_params["token"] = novo_token
                            with get_conn() as conn:
                                conn.execute("INSERT INTO sessoes (token, usuario, data_criacao) VALUES (?, ?, ?)", (novo_token, usr_input, str(datetime.now())))
                            
                            st.toast(f"Bem-vindo de volta, {usr_input}!", icon="👋")
                            st.rerun()
                        else:
                            st.error("⏳ Seu cadastro está pendente de aprovação. Solicite ao administrador a liberação do seu acesso.")
                    else:
                        st.error("❌ Usuário ou senha incorretos. Verifique suas credenciais.")
                else:
                    st.warning("Preencha todos os campos para fazer o login.")
                    
    with aba_cadastro:
        st.subheader("📝 Solicitar Novo Acesso Operacional")
        st.info("Nota: Após concluir o envio, seu cadastro ficará retido em uma fila de espera até que o administrador aprove.")
        with st.form("form_cadastro"):
            new_usr = st.text_input("Escolha um Nome de Usuário").strip()
            new_pass = st.text_input("Escolha uma Senha", type="password")
            pergunta = st.selectbox("Escolha uma pergunta de segurança para recuperação:", [
                "Qual o nome do seu primeiro animal de estimação?",
                "Qual a sua cidade natal?",
                "Qual o nome da sua mãe?",
                "Qual o nome do seu primeiro colégio?"
            ])
            resposta = st.text_input("Resposta da Pergunta de Segurança").strip().lower()
            btn_cadastrar = st.form_submit_button("Enviar Solicitação de Cadastro")
            
            if btn_cadastrar:
                if new_usr and new_pass and resposta:
                    status_inicial = 1 if new_usr.lower() == "admin" else 0
                    perfil_inicial = "Administrador" if new_usr.lower() == "admin" else "Operador"
                    try:
                        with get_conn() as conn:
                            conn.execute(
                                "INSERT INTO usuarios (usuario, senha_hash, pergunta_seguranca, resposta_seguranca_hash, aprovado, perfil) VALUES (?, ?, ?, ?, ?, ?)",
                                (new_usr, gerar_hash_senha(new_pass), pergunta, gerar_hash_senha(resposta), status_inicial, perfil_inicial)
                            )
                        disparar_sincronizacao()
                        if status_inicial == 1:
                            st.success("👑 Conta de administrador master criada! Vá para a aba Entrar e realize o login.")
                        else:
                            st.success(f"⏳ Solicitação enviada! O usuário '{new_usr}' foi colocado na fila de aceitação do Administrador.")
                    except:
                        st.error("❌ Esse nome de usuário já existe na base. Tente uma combinação diferente.")
                else:
                    st.warning("Todos os campos do formulário são obrigatórios.")
                    
    with aba_recuperar:
        st.subheader("== Redefinição de Credencial ==")
        usr_recup = st.text_input("Digite o usuário que deseja redefinir:").strip()
        
        if usr_recup:
            with get_conn() as conn:
                dados_usr = conn.execute("SELECT pergunta_seguranca, aprovado FROM usuarios WHERE usuario = ?", (usr_recup,)).fetchone()
            
            if dados_usr:
                st.info(f"Pergunta de Segurança: **{dados_usr[0]}**")
                resp_recup = st.text_input("Digite a sua resposta secreta:", type="password").strip().lower()
                nova_senha = st.text_input("Digite a sua Nova Senha:", type="password")
                
                if st.button("💾 Gravar Nova Senha"):
                    if resp_recup and nova_senha:
                        hash_resp = gerar_hash_senha(resp_recup)
                        with get_conn() as conn:
                            verif = conn.execute("SELECT usuario FROM usuarios WHERE usuario = ? AND resposta_seguranca_hash = ?", (usr_recup, hash_resp)).fetchone()
                        
                        if verif:
                            with get_conn() as conn:
                                conn.execute("UPDATE usuarios SET senha_hash = ? WHERE usuario = ?", (gerar_hash_senha(nova_senha), usr_recup))
                            disparar_sincronizacao()
                            st.success("✅ Senha redefinida com sucesso! Pode voltar para a tela de login.")
                        else:
                            st.error("❌ Resposta de segurança incorreta. Tente novamente.")
                    else:
                        st.warning("Preencha a resposta e a nova senha.")
            else:
                st.error("Usuário não encontrado na base do sistema.")

# ─────────────────────────────────────────────────────────────
# CONTEÚDO OPERACIONAL DO WMS (RODA APENAS SE AUTENTICADO)
# ─────────────────────────────────────────────────────────────
else:
    with st.sidebar:
        st.write(f"👤 Operador: **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ Nível: **{st.session_state['perfil_atual']}**")
        if st.button("🚪 Sair do Sistema (Logoff)", type="primary"):
            # Exclui o token de sessão do banco e da URL para segurança total
            if "token" in st.query_params:
                with get_conn() as conn:
                    conn.execute("DELETE FROM sessoes WHERE token = ?", (st.query_params["token"],))
                st.query_params.clear()
                
            st.session_state["autenticado"] = False
            st.session_state["usuario_atual"] = ""
            st.session_state["perfil_atual"] = ""
            st.rerun()

    df = listar_produtos()
    
    # ─── CONTROLE DE ACESSO DE TELAS (RBAC) ───
    tabs_disponiveis = ["📊 Painel", "⚡ Saídas/Entradas", "📋 INVENTÁRIO", "📜 Histórico"]
    is_admin = st.session_state.get("perfil_atual") == "Administrador"
    
    if is_admin:
        tabs_disponiveis.extend(["🧠 IA Analista", "⚙️ Config"])
        
    abas = st.tabs(tabs_disponiveis)
    aba_painel, aba_operacao, aba_contagem, aba_historico = abas[0], abas[1], abas[2], abas[3]
    
    # PAINEL PRINCIPAL
    with aba_painel:
        if not df.empty:
            df["valor_total"] = df["saldo_atual"] * df["valor_unitario"]
            
            with get_conn() as conn:
                cons = pd.read_sql("""
                    SELECT id_produto, SUM(ABS(quantidade)) as total 
                    FROM movimentacoes 
                    WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0)
                    GROUP BY id_produto
                """, conn)
                
            df = df.merge(cons, left_on='id', right_on='id_produto', how='left').fillna(0)
            df['consumo_diario'] = df['total'] / 30
            
            mask = df['consumo_diario'] > 0
            df['Runway'] = 999
            df.loc[mask, 'Runway'] = (df.loc[mask, 'saldo_atual'] / df.loc[mask, 'consumo_diario']).astype(int)
            
            def set_status(row):
                if row['saldo_atual'] <= 0: return '🔴 Ruptura'
                if row['saldo_atual'] < row['estoque_minimo']: return '🔴 Crítico'
                if row['Runway'] != 999 and row['Runway'] <= row['lead_time']: return '🟠 Risco'
                return '🟢 OK'
            df['Status'] = df.apply(set_status, axis=1)
            df['Runway_Txt'] = df['Runway'].apply(lambda x: "Sem consumo" if x == 999 else f"{x} dias")

            itens_criticos = int((df["saldo_atual"] < df["estoque_minimo"]).sum())
            if itens_criticos > 0:
                card_critico_style = 'background-color: rgba(239, 68, 68, 0.15); border-top: 4px solid #ef4444; color: #ef4444;'
            else:
                card_critico_style = 'background-color: rgba(16, 185, 129, 0.15); border-top: 4px solid #10b859; color: #10b859;'

            c1, c2, c3, c4 = st.columns([1,1,1,1])
            c1.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Categorias<br><b>{df["categoria"].nunique()}</b></div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Valor Total<br><b>R$ {df["valor_total"].sum():,.2f}</b></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="metric-card" style="{card_critico_style}">Itens Críticos/Ruptura<br><b>{itens_criticos}</b></div>', unsafe_allow_html=True)
            c4.markdown(f'<div class="metric-card" style="border-top: 4px solid #0052cc;">Giro Total<br><b>{int(df["total"].sum())} un</b></div>', unsafe_allow_html=True)

            st.divider()
            
            cp1, cp2 = st.columns([1, 1])
            with cp1:
                setores = ["Todos"] + list(df["categoria"].unique())
                setor_sel = st.selectbox("⚡ Filtrar por Setor:", setores)
            with cp2:
                busca_nome = st.text_input("🔍 Busca Rápida por Nome do Insumo:")
            
            df_filtrado = df.copy()
            if setor_sel != "Todos":
                df_filtrado = df_filtrado[df_filtrado["categoria"] == setor_sel]
            if busca_nome.strip():
                df_filtrado = df_filtrado[df_filtrado["nome"].str.contains(busca_nome, case=False)]

            st.subheader("📋 Posição de Estoque")
            
            def destacar_status(val):
                if '🔴' in str(val): return 'background-color: rgba(239, 68, 68, 0.35); color: #000000; font-weight: bold;'
                if '🟠' in str(val): return 'background-color: rgba(245, 158, 11, 0.35); color: #000000; font-weight: bold;'
                if '🟢' in str(val): return 'background-color: rgba(16, 185, 129, 0.35); color: #000000; font-weight: bold;'
                return ''

            display_df = df_filtrado[['Status', 'categoria', 'nome', 'saldo_atual', 'valor_unitario', 'estoque_minimo', 'Runway_Txt']].rename(
                columns={'categoria':'Setor', 'nome':'Produto', 'valor_unitario': 'Preço Médio', 'Runway_Txt':'Cobertura (Runway)'}
            )
            
            st.dataframe(
                display_df.style.map(destacar_status, subset=['Status']).format({'Preço Médio': 'R$ {:.2f}'}),
                hide_index=True, width='stretch'
            )

            st.divider()
            st.subheader("📊 Gráficos de Performance e Movimentação")
            g1, g2 = st.columns(2)
            df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
            
            with g1:
                st.markdown("##### 📊 Giro Total (Saídas) por Categoria")
                giro_setor = df.groupby("categoria")["total"].sum().reset_index().rename(columns={"categoria": "Setor", "total": "Movimentações"})
                if giro_setor["Movimentações"].sum() > 0:
                    st.bar_chart(data=giro_setor, x="Setor", y="Movimentações", width='stretch')
                else:
                    st.info("Ainda não há registros de saídas.")
                    
            with g2:
                st.markdown("##### 🏆 Top 5 Insumos Mais Consumidos (30 dias)")
                df_consumo_real = df[df["total"] > 0]
                if not df_consumo_real.empty:
                    top_consumo = df_consumo_real.nlargest(5, "total")[["nome", "total"]].rename(columns={"nome": "Insumo", "total": "Quantidade"})
                    st.bar_chart(data=top_consumo, x="Insumo", y="Quantidade", width='stretch')
                else:
                    st.info("Ainda não há consumo registrado.")

            st.divider()
            st.subheader("🛒 Sugestão de Reposição (Cálculo WMS)")
            df_filtrado["Minimo Ideal"] = (df_filtrado["consumo_diario"] * df_filtrado["lead_time"] * 1.2).astype(int)
            df_filtrado["Alvo"] = df_filtrado[["estoque_minimo", "Minimo Ideal"]].max(axis=1)
            df_filtrado["Sugestão Compra"] = (df_filtrado["Alvo"] - df_filtrado["saldo_atual"]).clip(lower=0)
            
            apenas_compras = st.checkbox("🛒 Mostrar apenas insumos com necessidade de compra urgente")
            df_compras = df_filtrado.copy()
            if apenas_compras:
                df_compras = df_compras[df_compras["Sugestão Compra"] > 0]
                
            st.dataframe(df_compras[["categoria", "nome", "lead_time", "saldo_atual", "Minimo Ideal", "Sugestão Compra"]].rename(columns={"categoria": "Setor", "nome": "Produto", "lead_time": "Entrega(d)", "saldo_atual": "Saldo", "Sugestão Compra": "Comprar"}), hide_index=True, width='stretch')

    # OPERAÇÃO (SAÍDAS E ENTRADAS)
    with aba_operacao:
        if not df.empty:
            col_e, col_s = st.columns(2)
            with col_e:
                with st.container(border=True):
                    st.subheader("⬇️ Registrar Entrada")
                    ops = dict(zip(df["nome"], df["id"]))
                    sel_e = st.selectbox("Produto", list(ops.keys()), key="e_p")
                    id_pe = ops[sel_e]
                    p_atual = df.loc[df["id"]==id_pe].iloc[0]
                    sal_e = int(p_atual["saldo_atual"])
                    pmp_antigo = float(p_atual["valor_unitario"])
                    
                    c1, c2 = st.columns([1, 1])
                    with c1: qe = st.number_input("Quantidade", min_value=1, key="e_q")
                    with c2: preco_compra = st.number_input("Preço Unit. de Compra (R$)", min_value=0.0, value=pmp_antigo, step=0.01, key="e_v")
                    obs_e = st.text_input("Nota/Fornecedor", key="e_obs")
                        
                    if st.button("Confirmar Entrada", type="secondary"):
                        total_novas_unidades = sal_e + qe
                        novo_pmp = ((sal_e * pmp_antigo) + (qe * preco_compra)) / total_novas_unidades if total_novas_unidades > 0 else preco_compra
                        with get_conn() as conn:
                            conn.execute("UPDATE produtos SET saldo_atual = saldo_atual + ?, valor_unitario = ? WHERE id = ?", (qe, novo_pmp, id_pe))
                            data = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
                            obs_completa = f"{obs_e} | Pago: R$ {preco_compra:.2f}/un" if obs_e.strip() else f"Pago: R$ {preco_compra:.2f}/un"
                            conn.execute("INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Entrada', ?, ?, ?)", (id_pe, data, qe, total_novas_unidades, obs_completa))
                        disparar_sincronizacao()
                        st.toast(f"📥 Entrada registrada! Novo PMP: R$ {novo_pmp:.2f}", icon="✅")
                        st.rerun()

            with col_s:
                with st.container(border=True):
                    st.subheader("📤 Registrar Saída")
                    sel = st.selectbox("Produto ", list(ops.keys()), key="s_p")
                    id_p = ops[sel]
                    max_s = int(df.loc[df["id"]==id_p, "saldo_atual"].values[0])
                    c1, c2 = st.columns([1, 2])
                    with c1: q = st.number_input("Quantidade", min_value=1, key="s_q")
                    with c2: obs_s = st.text_input("Observação/Destino", key="s_obs")
                    
                    bloquear_saida = q > max_s
                    if bloquear_saida: st.error(f"❌ Estoque Insuficiente! Saldo na prateleira: {max_s} un.")
                        
                    if st.button("Confirmar Saída", type="primary", disabled=bloquear_saida):
                        with get_conn() as conn:
                            conn.execute("UPDATE produtos SET saldo_atual = saldo_atual - ? WHERE id = ?", (q, id_p))
                            data = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
                            conn.execute("INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Saída', ?, ?, ?)", (id_p, data, -q, max_s - q, obs_s))
                        disparar_sincronizacao()
                        st.toast(f"📤 Baixa realizada com sucesso!", icon="🚀")
                        st.rerun()

    # AUDITORIA / CONTAGEM COM MARCADOR VISUAL INTELIGENTE
    with aba_contagem:
        st.subheader("📋 Auditoria de Inventário Diária/Semanal")
        if not df.empty:
            # Puxa a data de hoje e verifica quem já foi contado
            hoje = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y")
            with get_conn() as conn:
                query_hoje = f"SELECT id_produto FROM movimentacoes WHERE tipo = 'Contagem' AND data_hora LIKE '{hoje}%'"
                contados_hoje_df = pd.read_sql(query_hoje, conn)
            ids_contados_hoje = contados_hoje_df['id_produto'].tolist()
            
            with st.container(border=True):
                # Cria a lista do seletor aplicando a marca ✅ em quem já foi lido hoje
                ops = {}
                for _, row in df.iterrows():
                    nome_exib = f"✅ {row['nome']} (Auditado Hoje)" if row['id'] in ids_contados_hoje else row['nome']
                    ops[nome_exib] = row['id']
                
                sel_c = st.selectbox("Selecione o Insumo para Contagem:", list(ops.keys()), key="c_p")
                id_pc = ops[sel_c]
                s_sis = int(df.loc[df["id"]==id_pc, "saldo_atual"].values[0])
                st.metric("Saldo Atual no Sistema", f"{s_sis} un")
                f_cont = st.number_input("Quantidade Física Contada", min_value=0, step=1, key="c_q")
                diff = f_cont - s_sis
                
                if st.button("💾 Gravar e Sincronizar Inventário", use_container_width=True, type="primary"):
                    with get_conn() as conn:
                        conn.execute("UPDATE produtos SET saldo_atual = ? WHERE id = ?", (f_cont, id_pc))
                        data = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")
                        conn.execute("INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Contagem', ?, ?, 'Inventário Semanal')", (id_pc, data, diff, f_cont))
                    disparar_sincronizacao()
                    st.toast(f"📋 Inventário gravado!", icon="💾")
                    st.rerun()

            # Resumo visual extra de itens concluídos hoje
            if ids_contados_hoje:
                st.success(f"📌 Excelente! Você já auditou {len(set(ids_contados_hoje))} insumos na data de hoje ({hoje}).")

            st.divider()
            st.subheader("📉 Relatório de Ajustes e Perdas do Inventário")
            prod_lista = ["Todos os Insumos"] + list(df["nome"].unique())
            prod_aud_sel = st.selectbox("Filtrar auditorias por item específico:", prod_lista)
            
            query_hist = """
                SELECT m.data_hora as 'Data/Hora', p.nome as 'Produto', 
                       (m.saldo_resultante - m.quantidade) as 'Saldo Anterior',
                       m.saldo_resultante as 'Contagem Física',
                       m.quantidade as 'Divergência'
                FROM movimentacoes m 
                JOIN produtos p ON p.id = m.id_produto
                WHERE m.tipo = 'Contagem'
            """
            if prod_aud_sel != "Todos os Insumos":
                query_hist += f" AND p.nome = '{prod_aud_sel}'"
            query_hist += " ORDER BY m.id DESC LIMIT 15"
            
            with get_conn() as conn:
                hist_inv = pd.read_sql(query_hist, conn)
            if not hist_inv.empty:
                def cor_divergencia(val):
                    if val < 0: return 'color: #ef4444; font-weight: bold;'
                    if val > 0: return 'color: #10b859; font-weight: bold;'
                    return 'color: #94a3b8;'
                st.dataframe(hist_inv.style.map(cor_divergencia, subset=['Divergência']), hide_index=True, width='stretch')

    # HISTÓRICO
    with aba_historico:
        st.subheader("📜 Histórico de Movimentações")
        mv = listar_movimentacoes()
        if not mv.empty:
            st.markdown("##### 📈 Gráfico de Auditoria e Evolução de Preços")
            if not df.empty:
                item_analise = st.selectbox("Selecione o Insumo para ver a Curva de Custos:", list(df["nome"].unique()))
                entradas_item = mv[(mv["produto"] == item_analise) & (mv["tipo"] == "Entrada")].copy()
                
                if not entradas_item.empty:
                    def extrair_preco(obs):
                        try:
                            if "Pago: R$" in str(obs):
                                return float(str(obs).split("Pago: R$ ")[1].split("/un")[0])
                        except: pass
                        return None
                    
                    entradas_item["Preço de Compra (R$)"] = entradas_item["observacao"].apply(extrair_preco)
                    entradas_item = entradas_item.dropna(subset=["Preço de Compra (R$)"]).iloc[::-1]
                    if not entradas_item.empty: st.line_chart(data=entradas_item, x="data_hora", y="Preço de Compra (R$)", width='stretch')
                else:
                    st.info("Ainda não existem entradas registradas para este produto.")
            
            st.divider()
            st.markdown("##### 📋 Histórico Geral das Movimentações")
            mv['Mês/Ano'] = mv['data_hora'].apply(lambda x: x.split()[0][3:])
            mes_selecionado = st.selectbox("Filtrar por Período:", sorted(mv['Mês/Ano'].unique(), reverse=True))
            st.dataframe(mv[mv['Mês/Ano'] == mes_selecionado].drop(columns=['Mês/Ano']), use_container_width=True, hide_index=True)

    # CONTEÚDO EXCLUSIVO DO ADMINISTRADOR (IA E CONFIG)
    if is_admin:
        aba_ia, aba_gestao = abas[4], abas[5]
        
        with aba_ia:
            st.subheader("🧠 Assistente IA de Suprimentos")
            try:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                modelos_validos = [m.name.replace('models/', '') for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                modelo_selecionado = st.selectbox("🤖 Selecione a versão do modelo de IA:", modelos_validos, index=0)
                
                if st.button("✨ Gerar Diagnóstico Logístico"):
                    if not df.empty:
                        with st.spinner(f"Analisando dados com a versão {modelo_selecionado}..."):
                            with get_conn() as conn:
                                cons = conn.execute("SELECT id_produto, SUM(ABS(quantidade)) FROM movimentacoes WHERE tipo='Saída' OR (tipo='Contagem' AND quantidade < 0) GROUP BY id_produto").fetchall()
                            cons_dict = dict(cons)
                            df['consumo_mensal'] = df['id'].map(cons_dict).fillna(0).astype(int)
                            
                            mod = genai.GenerativeModel(modelo_selecionado)
                            prompt = f"Analise o estoque logístico:\n{df[['categoria', 'nome', 'saldo_atual', 'estoque_minimo', 'lead_time', 'consumo_mensal']].to_string(index=False)}\nEntregue: Resumo de saúde, riscos de ruptura antes do lead time e sugestão de compras."
                            st.write(mod.generate_content(prompt).text)
            except Exception as e:
                st.error(f"Erro de comunicação com a API do Google: {e}") 

        with aba_gestao:
            st.markdown("### 👑 Painel de Aprovações de Novos Operadores")
            with get_conn() as conn:
                pendentes = pd.read_sql("SELECT usuario, pergunta_seguranca FROM usuarios WHERE aprovado = 0", conn)
            
            if not pendentes.empty:
                st.dataframe(pendentes, use_container_width=True, hide_index=True)
                col_sel, col_perf, col_act = st.columns([2, 2, 2])
                with col_sel:
                    usr_alvo = st.selectbox("Selecione o usuário:", list(pendentes["usuario"]))
                with col_perf:
                    perfil_alvo = st.selectbox("Nível de Acesso:", ["Operador", "Administrador"])
                with col_act:
                    c_ap, c_rec = st.columns(2)
                    with c_ap:
                        if st.button("✅ Aprovar", use_container_width=True):
                            with get_conn() as conn:
                                conn.execute("UPDATE usuarios SET aprovado = 1, perfil = ? WHERE usuario = ?", (perfil_alvo, usr_alvo))
                            disparar_sincronizacao()
                            st.success(f"Operador '{usr_alvo}' liberado como {perfil_alvo}!")
                            st.rerun()
                    with c_rec:
                        if st.button("❌ Recusar", use_container_width=True):
                            with get_conn() as conn:
                                conn.execute("DELETE FROM usuarios WHERE usuario = ?", (usr_alvo,))
                            disparar_sincronizacao()
                            st.warning(f"A solicitação de '{usr_alvo}' foi excluída.")
                            st.rerun()
            else:
                st.success("✅ Nenhuma solicitação de cadastro pendente na fila.")
            st.divider()

            a1, a2, a3 = st.tabs(["➕ Novo Insumo", "✏️ Editar Insumo", "🗑️ Excluir Insumo"])
            with a1:
                with st.form("new_p"):
                    n = st.text_input("Nome do Insumo")
                    c = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
                    m = st.number_input("Mínimo", value=10)
                    l = st.number_input("Lead Time (Dias)", value=3)
                    v = st.number_input("Valor Inicial Un. (R$)", value=0.0)
                    if st.form_submit_button("Cadastrar"):
                        if n.strip():
                            cadastrar_produto(n.strip(), m, v, c, l)
                            disparar_sincronizacao()
                            st.toast(f"➕ Cadastrado!", icon="✨")
                            st.rerun()
                        
            with a2:
                if not df.empty:
                    op_e = dict(zip(df["nome"], df["id"]))
                    s_e = st.selectbox("Produto p/ Editar", list(op_e.keys()))
                    id_e = op_e[s_e]
                    p_at = df[df["id"]==id_e].iloc[0]
                    with st.form("edit_p"):
                        en = st.text_input("Nome", value=p_at["nome"])
                        ec = st.selectbox("Setor", ["Limpeza", "Copa", "EPI", "Escritório", "Geral"])
                        em = st.number_input("Mínimo", value=int(p_at["estoque_minimo"]))
                        el = st.number_input("Lead Time", value=int(p_at["lead_time"]))
                        ev = st.number_input("Preço Médio", value=float(p_at["valor_unitario"]))
                        if st.form_submit_button("Atualizar"):
                            editar_produto(id_e, en, em, ev, ec, el)
                            disparar_sincronizacao()
                            st.toast(f"✏️ Atualizado!", icon="⚙️")
                            st.rerun()
                            
            with a3:
                if not df.empty:
                    op_d = dict(zip(df["nome"], df["id"]))
                    s_d = st.selectbox("Selecione para Excluir", list(op_d.keys()))
                    id_d = op_d[s_d]
                    confirmar = st.checkbox("Confirmo que pretendo apagar este insumo e destruir seu histórico.")
                    if st.button("🗑️ Eliminar Definitivamente", type="primary", disabled=not confirmar):
                        try:
                            deletar_produto(id_d)
                            disparar_sincronizacao()
                            st.toast(f"🗑️ Removido!", icon="🗑️")
                            st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")