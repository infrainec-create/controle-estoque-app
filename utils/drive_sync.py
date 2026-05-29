import os
import sqlite3
import threading
from datetime import datetime, timezone
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

def parsed_drive_time(iso_str):
    if not iso_str:
        return None
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(iso_str)
    except Exception:
        try:
            return datetime.strptime(iso_str.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def obter_local_mtime():
    if os.path.exists(DB_PATH):
        mtime = os.path.getmtime(DB_PATH)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    return None

def obter_metadados_drive():
    try:
        servico = obter_servico_drive()
        query = f"name='{os.path.basename(DB_PATH)}' and '{FOLDER_ID}' in parents and trashed=false"
        res = servico.files().list(
            q=query, 
            fields="files(id, name, modifiedTime, size)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        files = res.get('files', [])
        if files:
            return files[0]
    except Exception:
        pass
    return None

def salvar_ultimo_sync_time_local(mtime_drive_str):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES ('ultimo_sync_drive_mtime', ?)",
                (mtime_drive_str,)
            )
    except Exception:
        pass

def obter_ultimo_sync_time_local():
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'ultimo_sync_drive_mtime'").fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return None

def sincronizar_banco_na_inicializacao():
    """
    Sincroniza o banco local com o banco do Drive no início da sessão.
    Se o banco do Drive for mais recente, realiza o download.
    """
    # Se a sincronização estiver desativada no banco de dados, não faz nada
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'drive_sync_ativo'").fetchone()
            if row and row[0] == '0':
                return
    except Exception:
        pass

    try:
        meta_drive = obter_metadados_drive()
        if not meta_drive:
            return
            
        mtime_drive_str = meta_drive.get('modifiedTime')
        mtime_drive = parsed_drive_time(mtime_drive_str)
        
        if not os.path.exists(DB_PATH):
            descarregar_do_drive()
            return
            
        mtime_local = obter_local_mtime()
        
        if mtime_drive and mtime_local:
            # Se o banco do Drive for mais recente por mais de 2 segundos, baixa
            if (mtime_drive - mtime_local).total_seconds() > 2:
                descarregar_do_drive()
    except Exception as e:
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                    (f"Erro na sincronização de inicialização: {e}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
        except Exception:
            pass

def executar_sincronizacao_drive():
    try:
        servico = obter_servico_drive()
        
        # 1. Verificação de Conflitos para evitar sobrescrever dados mais recentes da nuvem
        meta_drive = obter_metadados_drive()
        if meta_drive:
            mtime_drive_str = meta_drive.get('modifiedTime')
            ultimo_sync_local = obter_ultimo_sync_time_local()
            
            if ultimo_sync_local and mtime_drive_str != ultimo_sync_local:
                mtime_drive = parsed_drive_time(mtime_drive_str)
                mtime_local_sync = parsed_drive_time(ultimo_sync_local)
                
                # Se o arquivo na nuvem é mais recente do que a última sincronização que este cliente fez
                if mtime_drive and mtime_local_sync and (mtime_drive - mtime_local_sync).total_seconds() > 2:
                    with get_conn() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                            ("Conflito detectado! O banco de dados na nuvem foi modificado por outra sessão. Recarregue a página ou faça download manual.", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                        )
                    return
        
        # 2. Upload Seguro do Banco de Dados (.db) com Retry (3 tentativas com Backoff)
        import time
        query = f"name='{os.path.basename(DB_PATH)}' and '{FOLDER_ID}' in parents and trashed=false"
        
        upload_res = None
        for tentativa in range(3):
            try:
                files = servico.files().list(
                    q=query, 
                    fields="files(id)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute().get('files', [])
                
                media = MediaFileUpload(DB_PATH, mimetype='application/x-sqlite3', resumable=True)
                if files: 
                    upload_res = servico.files().update(
                        fileId=files[0]['id'], 
                        media_body=media,
                        fields="modifiedTime",
                        supportsAllDrives=True
                    ).execute()
                else: 
                    upload_res = servico.files().create(
                        body={'name': os.path.basename(DB_PATH), 'parents': [FOLDER_ID]}, 
                        media_body=media,
                        fields="modifiedTime",
                        supportsAllDrives=True
                    ).execute()
                break # Sucesso, sai do loop
            except Exception as e:
                if tentativa < 2:
                    time.sleep(2 ** tentativa)
                else:
                    raise e
            
        # Salva o novo timestamp de sincronização localmente
        if upload_res and 'modifiedTime' in upload_res:
            salvar_ultimo_sync_time_local(upload_res['modifiedTime'])
        
        # 3. Geração e Extração dos CSVs para o Looker Studio com Retry
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
            """, conn)
            
        for df, name in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            q = f"name='{name}' and '{FOLDER_ID}' in parents"
            for tentativa in range(3):
                try:
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
                    break # Sucesso
                except Exception as e:
                    if tentativa < 2:
                        time.sleep(2 ** tentativa)
                    else:
                        raise e
            
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
            msg = "Cota de armazenamento excedida. Contas de Serviço GCP não possuem cota própria no Drive pessoal. Use um Drive Compartilhado ou configure OAuth 2.0."
            
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                (f"Erro de comunicação Drive: {msg}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            )

def disparar_sincronizacao():
    st.cache_data.clear()
    
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'drive_sync_ativo'").fetchone()
            if row and row[0] == '0':
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                    ("Sincronização na nuvem desativada localmente.", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
                return
    except Exception:
        pass

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
            fields="files(id, modifiedTime)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        if res.get('files', []):
            file_meta = res['files'][0]
            req = servico.files().get_media(fileId=file_meta['id'])
            
            temp_path = DB_PATH + ".tmp"
            with open(temp_path, "wb") as f:
                load = MediaIoBaseDownload(f, req)
                done = False
                while not done: 
                    _, done = load.next_chunk()
            
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            os.rename(temp_path, DB_PATH)
            
            for suffix in ["-wal", "-shm"]:
                extra_file = DB_PATH + suffix
                if os.path.exists(extra_file):
                    try: os.remove(extra_file)
                    except: pass
            
            salvar_ultimo_sync_time_local(file_meta.get('modifiedTime'))
            
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 1, ?, ?)",
                    ("Banco de dados baixado da nuvem com sucesso!", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
            st.cache_data.clear()
            return True
    except Exception as e:
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('global', 0, ?, ?)",
                    (f"Erro ao baixar da nuvem: {e}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
                )
        except:
            pass
        return False
    return False
