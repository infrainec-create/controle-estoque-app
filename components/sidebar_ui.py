import streamlit as st
import streamlit.components.v1 as components
from database.connection import get_conn
from database.queries import registrar_log_auditoria

def render_sidebar_ui(tabs_disponiveis=None, df=None):
    """
    Renderiza a barra lateral (sidebar) com informações do operador,
    menu de navegação vertical com badges dinâmicos de alerta,
    botão de logoff, cronômetro de inatividade da sessão e status da nuvem.
    """
    aba_selecionada = None
    with st.sidebar:
        st.markdown("<h2 style='text-align: center; margin-top: 5px; margin-bottom: 15px; font-weight: 800; font-size: 1.35rem;'>📦 WMS 5.0</h2>", unsafe_allow_html=True)
        st.write(f"👤 Operador: **{st.session_state.get('usuario_atual', 'Operador')}**")
        st.write(f"🛡️ Nível: **{st.session_state.get('perfil_atual', 'Operador')}**")
        
        # Leitura de métricas preventivas para exibir Badges visuais no Menu Lateral
        n_alertas = 0
        if df is not None and not df.empty:
            n_rupturas = int((df['saldo_atual'] <= 0).sum())
            n_criticos = int(((df['saldo_atual'] > 0) & (df['saldo_atual'] < df['estoque_minimo'])).sum())
            n_alertas = n_rupturas + n_criticos

        # Menu de Navegação Vertical (Abas no Painel Lateral com Badges)
        if tabs_disponiveis:
            st.markdown("---")
            st.markdown("<p style='font-size: 0.75rem; font-weight: 700; text-transform: uppercase; color: gray; letter-spacing: 1px; margin-bottom: 8px;'>📋 Navegação Principal</p>", unsafe_allow_html=True)
            
            labels_map = {}
            options_display = []
            
            for t in tabs_disponiveis:
                if t == "📊 Painel" and n_alertas > 0:
                    lbl = f"📊 Painel ({n_alertas} 🚨)"
                else:
                    lbl = t
                labels_map[lbl] = t
                options_display.append(lbl)
            
            current_canonical = st.session_state.get("aba_ativa", tabs_disponiveis[0])
            current_disp = options_display[0]
            for lbl, can in labels_map.items():
                if can == current_canonical:
                    current_disp = lbl
                    break
                    
            idx_default = options_display.index(current_disp) if current_disp in options_display else 0
            
            selected_disp = st.radio(
                "Menu Principal",
                options=options_display,
                index=idx_default,
                key="sidebar_nav_radio",
                label_visibility="collapsed"
            )
            
            aba_selecionada = labels_map.get(selected_disp, current_canonical)
            st.session_state["aba_ativa"] = aba_selecionada
        
        st.markdown("---")
        
        # Botão de Logoff do Sistema
        if st.button("🚪 Sair do Sistema (Logoff)", type="primary", use_container_width=True):
            registrar_log_auditoria(st.session_state["usuario_atual"], "Logoff no Sistema", "Operador encerrou a sessão manualmente.")
            
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
            
        # Cronômetro de Sessão regressivo em tempo real
        timer_html = f"<!-- timestamp: {int(st.session_state.get('ultimo_acesso', 0))} -->\n" + """
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

    return aba_selecionada
