import streamlit as st
import streamlit.components.v1 as components
from database.connection import get_conn
from database.queries import registrar_log_auditoria

def render_sidebar_ui():
    """
    Renderiza a barra lateral (sidebar) com informações do operador,
    botão de logoff, cronômetro de inatividade da sessão e status da nuvem.
    """
    with st.sidebar:
        st.write(f"👤 Operador: **{st.session_state['usuario_atual']}**")
        st.write(f"🛡️ Nível: **{st.session_state['perfil_atual']}**")
        
        # Botão de Logoff do Sistema
        if st.button("🚪 Sair do Sistema (Logoff)", type="primary"):
            # Registrar log de auditoria antes de limpar a sessão
            registrar_log_auditoria(st.session_state["usuario_atual"], "Logoff no Sistema", "Operador encerrou a sessão manualmente.")
            
            # Deletar sessão persistente se existir
            session_token = st.query_params.get("session")
            if session_token:
                try:
                    with get_conn() as conn:
                        conn.execute("DELETE FROM sessoes WHERE token = ?", (session_token,))
                except Exception:
                    pass
                st.query_params.clear()
                
            st.session_state["autenticado"] = False
            st.session_state["usuario_atual"] = ""
            st.session_state["perfil_atual"] = ""
            if "ultimo_acesso" in st.session_state:
                del st.session_state["ultimo_acesso"]
            st.rerun()
            
        # Cronômetro de Sessão regressivo em tempo real (Iframe HTML/JS)
        timer_html = f"<!-- timestamp: {int(st.session_state['ultimo_acesso'])} -->\n" + """
        <div style="
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            padding: 10px 14px;
            background-color: rgba(255, 75, 75, 0.08);
            border-radius: 10px;
            border: 1px solid rgba(255, 75, 75, 0.2);
            text-align: center;
            margin-top: 5px;
            margin-bottom: 10px;
        ">
            <span style="font-size: 0.72rem; color: #888; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; display: block; margin-bottom: 2px;">
                ⏳ Tempo de Sessão Ativa
            </span>
            <div id="countdown" style="font-size: 1.55rem; font-weight: 700; color: #FF4B4B; font-variant-numeric: tabular-nums;">
                30:00
            </div>
            <span style="font-size: 0.65rem; color: #777; display: block; margin-top: 2px;">
                Reseta automaticamente ao interagir
            </span>
        </div>
        <script>
            var duration = 1800; // 30 minutos em segundos
            var timer = duration;
            var display = document.getElementById('countdown');
            
            var countdownInterval = setInterval(function () {
                var minutes = parseInt(timer / 60, 10);
                var seconds = parseInt(timer % 60, 10);

                minutes = minutes < 10 ? "0" + minutes : minutes;
                seconds = seconds < 10 ? "0" + seconds : seconds;

                display.textContent = minutes + ":" + seconds;

                if (--timer < 0) {
                    clearInterval(countdownInterval);
                    try {
                        window.parent.location.reload();
                    } catch (e) {
                        window.location.reload();
                    }
                }
            }, 1000);
        </script>
        """
        components.html(timer_html, height=105)
            
        # Leitura reativa do status de sincronia assíncrona gravado no SQLite
        if st.session_state.get("db_sincronizado") == "local":
            st.caption("🟡 Sincronização Desativada (Modo Offline)")
        else:
            try:
                with get_conn() as conn:
                    status_row = conn.execute("SELECT sucesso, mensagem, timestamp FROM status_sincronismo WHERE chave = 'global'").fetchone()
                
                if status_row:
                    sucesso, mensagem, timestamp_str = status_row
                    if sucesso == 1:
                        if "segundo plano" in mensagem:
                            st.caption(f"⏳ {mensagem}")
                        else:
                            st.caption(f"🟢 {mensagem} ({timestamp_str})")
                    else:
                        st.error(f"⚠️ {mensagem} ({timestamp_str})")
            except Exception:
                pass
