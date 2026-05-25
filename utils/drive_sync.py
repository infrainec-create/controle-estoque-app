import os
import sqlite3
import threading
from datetime import datetime
from io import BytesIO
import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
from database.connection import get_conn, DB_PATH

try:
    FOLDER_ID = st.secrets["FOLDER_ID"]
except Exception:
    FOLDER_ID = "MOCK_FOLDER_ID"

def obter_servico_drive():
    if "gcp_service_account" not in st.secrets:
        raise Exception("Credenciais do Google Cloud não encontradas no arquivo secrets.toml.")
        
    info_chaves = dict(st.secrets["gcp_service_account"])
    p_key = info_chaves.get("private_key", "")
    
    if info_chaves.get("project_id") == "seu-projeto-gcp" or "sua_chave_privada_aqui" in p_key:
        raise Exception("Credenciais de exemplo detectadas. Configure o secrets.toml com suas chaves reais do GCP para ativar a nuvem.")
        
    if "\\n" in p_key:
        info_chaves["private_key"] = p_key.replace("\\n", "\n")
        
    try:
        credenciais = service_account.Credentials.from_service_account_info(info_chaves)
        return build('drive', 'v3', credentials=credenciais)
    except Exception as e:
        raise Exception(f"Falha ao interpretar arquivo PEM: {e}")

def executar_sincronizacao_drive():
    try:
        servico = obter_servico_drive()
        
        # 1. Upload Seguro do Banco de Dados (.db)
        query = f"name='{os.path.basename(DB_PATH)}' and '{FOLDER_ID}' in parents and trashed=false"
        files = servico.files().list(
            q=query, 
            fields="files(id)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute().get('files', [])
        
        media = MediaFileUpload(DB_PATH, mimetype='application/x-sqlite3', resumable=True)
        if files: 
            servico.files().update(
                fileId=files[0]['id'], 
                media_body=media,
                supportsAllDrives=True
            ).execute()
        else: 
            servico.files().create(
                body={'name': os.path.basename(DB_PATH), 'parents': [FOLDER_ID]}, 
                media_body=media,
                supportsAllDrives=True
            ).execute()
        
        # 2. Geração e Extração dos CSVs para o Looker Studio
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
            """, conn)
            
        for df, name in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            q = f"name='{name}' and '{FOLDER_ID}' in parents"
            fs = servico.files().list(
                q=q,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute().get('files', [])
            
            m = MediaIoBaseUpload(BytesIO(df.to_csv(index=False).encode('utf-8-sig')), mimetype='text/csv')
            if fs: 
                servico.files().update(
                    fileId=fs[0]['id'], 
                    media_body=m,
                    supportsAllDrives=True
                ).execute()
            else: 
                servico.files().create(
                    body={'name': name, 'parents': [FOLDER_ID]}, 
                    media_body=m,
                    supportsAllDrives=True
                ).execute()
            
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                ("Nuvem sincronizada com sucesso!", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            )
    except sqlite3.OperationalError as e:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                (f"Banco de dados ocupado: {e}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            )
    except Exception as e:
        msg = str(e)
        if "storageQuotaExceeded" in msg or "do not have storage quota" in msg:
            msg = "Cota de armazenamento excedida. Contas de Serviço GCP não possuem cota própria no Drive pessoal. Para resolver, certifique-se de estar utilizando um 'Drive Compartilhado' (Shared Drive) do Google Workspace com acesso de Colaborador para a conta de serviço, ou mude para autenticação via OAuth 2.0."
            
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                (f"Erro de comunicação Drive: {msg}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            )

def disparar_sincronizacao():
    st.cache_data.clear()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
            ("Sincronizando em segundo plano...", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        )
    threading.Thread(target=executar_sincronizacao_drive).start()

def descarregar_do_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{os.path.basename(DB_PATH)}' and '{FOLDER_ID}' in parents and trashed=false"
        res = servico.files().list(
            q=query, 
            fields="files(id)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        if res.get('files', []):
            req = servico.files().get_media(fileId=res['files'][0]['id'])
            with open(DB_PATH, "wb") as f:
                load = MediaIoBaseDownload(f, req)
                done = False
                while not done: _, done = load.next_chunk()
            return True
    except: 
        return False
    return False
