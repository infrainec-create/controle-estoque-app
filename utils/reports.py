import io
import pandas as pd
from datetime import datetime
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

def formatar_aba_excel(ws, title_color="1E3A8A"):
    """
    Aplica formatação visual premium (cabeçalhos coloridos, auto-ajuste e bordas) a uma aba do Excel.
    """
    # Cabeçalho premium (Fonte branca, negrito, centralizado, preenchimento azul escuro)
    header_fill = PatternFill(start_color=title_color, end_color=title_color, fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center")
    
    thin_border = Border(
        left=Side(style='thin', color='D1D5DB'),
        right=Side(style='thin', color='D1D5DB'),
        top=Side(style='thin', color='D1D5DB'),
        bottom=Side(style='thin', color='D1D5DB')
    )
    
    # Formatação dos cabeçalhos (Linha 1)
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center
        cell.border = thin_border
    
    # Formatação das linhas de dados
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = thin_border
            # Se for texto, alinha à esquerda, se for número/data, centraliza
            if isinstance(cell.value, str):
                cell.alignment = align_left
            else:
                cell.alignment = align_center
                
    # Auto-ajuste de largura de colunas
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_to_check = cell.value or ''
            # Se for float formatado em dinheiro, adiciona tamanho para R$
            val_str = str(val_to_check)
            if isinstance(val_to_check, float) and cell.number_format and "R$" in cell.number_format:
                val_str = f"R$ {val_to_check:,.2f}"
            max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

def gerar_excel_estoque(df):
    """
    Gera arquivo Excel (.xlsx) premium contendo a posição de estoque e valuation de ativos.
    """
    buffer = io.BytesIO()
    
    # Prepara dados limpos para exportação
    df_export = df.copy()
    df_export["Valor Total Ativo (R$)"] = df_export["saldo_atual"] * df_export["valor_unitario"]
    
    # Renomeia colunas para cabeçalhos amigáveis em português
    df_export = df_export.rename(columns={
        "id": "ID Produto",
        "nome": "Insumo / Item",
        "saldo_atual": "Saldo Atual",
        "estoque_minimo": "Estoque Mínimo",
        "valor_unitario": "Valor Unitário (R$)",
        "categoria": "Setor / Categoria",
        "lead_time": "Lead Time (Dias)"
    })
    
    # Ordena colunas
    colunas_ordenadas = ["ID Produto", "Setor / Categoria", "Insumo / Item", "Saldo Atual", "Estoque Mínimo", "Valor Unitário (R$)", "Valor Total Ativo (R$)", "Lead Time (Dias)"]
    df_export = df_export[colunas_ordenadas]
    
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_export.to_excel(writer, sheet_name='Valuation de Estoque', index=False)
        
        workbook = writer.book
        worksheet = writer.sheets['Valuation de Estoque']
        
        # Formata colunas de valor
        for row in range(2, worksheet.max_row + 1):
            cell_unit = worksheet.cell(row=row, column=6) # Valor Unitário
            cell_total = worksheet.cell(row=row, column=7) # Valor Total
            cell_unit.number_format = 'R$ #,##0.00'
            cell_total.number_format = 'R$ #,##0.00'
            
        # Adiciona linha de totais na base
        max_row = worksheet.max_row
        totals_row = max_row + 2
        
        # Estilo de totalização
        font_total = Font(name="Calibri", size=11, bold=True, color="000000")
        fill_total = PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid")
        border_total = Border(
            top=Side(style='thin', color='9CA3AF'),
            bottom=Side(style='double', color='000000')
        )
        
        worksheet.cell(row=totals_row, column=3, value="PATRIMÔNIO TOTAL:").font = font_total
        worksheet.cell(row=totals_row, column=3).alignment = Alignment(horizontal="right")
        
        # Fórmulas de totalização do Excel
        cell_sum_saldo = worksheet.cell(row=totals_row, column=4, value=f"=SUM(D2:D{max_row})")
        cell_sum_total = worksheet.cell(row=totals_row, column=7, value=f"=SUM(G2:G{max_row})")
        
        for c_idx in range(1, 9):
            c_cell = worksheet.cell(row=totals_row, column=c_idx)
            c_cell.font = font_total
            c_cell.fill = fill_total
            c_cell.border = border_total
            
        cell_sum_saldo.alignment = Alignment(horizontal="center")
        cell_sum_total.number_format = 'R$ #,##0.00'
        cell_sum_total.alignment = Alignment(horizontal="center")
        
        formatar_aba_excel(worksheet, title_color="1E3A8A")
        
    buffer.seek(0)
    return buffer.getvalue()

def gerar_excel_movimentacoes(mv):
    """
    Gera o extrato de movimentações do sistema em formato Excel formatado.
    """
    buffer = io.BytesIO()
    
    df_export = mv.copy()
    df_export = df_export.rename(columns={
        "id": "ID Lançamento",
        "produto": "Insumo / Item",
        "data_hora": "Data/Hora",
        "tipo": "Tipo Operação",
        "quantidade": "Qtd. Movimentada",
        "saldo_resultante": "Saldo Resultante",
        "observacao": "Detalhes / Motivo"
    })
    
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_export.to_excel(writer, sheet_name='Histórico de Fluxo', index=False)
        formatar_aba_excel(writer.sheets['Histórico de Fluxo'], title_color="0F766E") # Cor verde azulado
        
    buffer.seek(0)
    return buffer.getvalue()

def gerar_excel_auditoria(logs):
    """
    Gera o log completo de auditoria do sistema em formato Excel formatado para compliance.
    """
    buffer = io.BytesIO()
    
    df_export = logs.copy()
    df_export = df_export.rename(columns={
        "id": "ID Registro",
        "usuario": "Operador",
        "acao": "Tipo de Ação",
        "data_hora": "Data/Hora",
        "detalhes": "Detalhes Operacionais"
    })
    
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_export.to_excel(writer, sheet_name='Logs de Auditoria', index=False)
        formatar_aba_excel(writer.sheets['Logs de Auditoria'], title_color="374151") # Cor cinza escuro
        
    buffer.seek(0)
    return buffer.getvalue()

def gerar_html_pdf_estoque(df, mv, logs):
    """
    Compila um relatório executivo de alta fidelidade visual (HTML) otimizado para salvamento em PDF / Impressão.
    """
    # Cálculos rápidos
    total_itens = len(df)
    valor_patrimonio = (df["saldo_atual"] * df["valor_unitario"]).sum()
    rupturas = (df["saldo_atual"] <= 0).sum()
    criticos = ((df["saldo_atual"] < df["estoque_minimo"]) & (df["saldo_atual"] > 0)).sum()
    
    data_geracao = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
    
    # Converte DataFrames em tabelas HTML limpas
    df_est = df.copy()
    df_est["Valor Total (R$)"] = df_est["saldo_atual"] * df_est["valor_unitario"]
    df_est = df_est.rename(columns={
        "categoria": "Setor",
        "nome": "Insumo",
        "saldo_atual": "Saldo",
        "estoque_minimo": "Mínimo",
        "valor_unitario": "Preço Un.",
        "lead_time": "Lead Time"
    })
    
    # Formatação condicional para status no HTML
    def get_status_badge(row):
        saldo = row["Saldo"]
        minimo = row["Mínimo"]
        if saldo <= 0:
            return '<span class="badge badge-danger">RUPTURA</span>'
        if saldo < minimo:
            return '<span class="badge badge-warning">CRÍTICO</span>'
        return '<span class="badge badge-success">OK</span>'
        
    df_est["Status"] = df_est.apply(get_status_badge, axis=1)
    
    # Formata moedas
    df_est["Preço Un."] = df_est["Preço Un."].apply(lambda x: f"R$ {x:,.2f}")
    df_est["Valor Total (R$)"] = df_est["Valor Total (R$)"].apply(lambda x: f"R$ {x:,.2f}")
    
    tb_estoque_html = df_est[["Setor", "Insumo", "Saldo", "Mínimo", "Preço Un.", "Valor Total (R$)", "Status"]].to_html(index=False, escape=False, classes="table")
    
    # Últimas 10 movimentações no HTML
    df_mov = mv.head(10).copy()
    df_mov = df_mov.rename(columns={
        "data_hora": "Data/Hora",
        "produto": "Insumo",
        "tipo": "Operação",
        "quantidade": "Qtd",
        "saldo_resultante": "Saldo Pos-Mov",
        "observacao": "Observação"
    })
    tb_mov_html = df_mov[["Data/Hora", "Insumo", "Operação", "Qtd", "Saldo Pos-Mov", "Observação"]].to_html(index=False, classes="table")
    
    # Template HTML Premium
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Relatório Executivo WMS 5.0</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
            
            body {{
                font-family: 'Inter', sans-serif;
                color: #1F2937;
                background-color: #F9FAFB;
                margin: 0;
                padding: 40px;
                line-height: 1.5;
            }}
            
            .report-container {{
                max-width: 900px;
                margin: 0 auto;
                background-color: #FFFFFF;
                padding: 40px;
                border-radius: 12px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05);
                border: 1px solid #E5E7EB;
            }}
            
            .header-table {{
                width: 100%;
                border-bottom: 2px solid #1E3A8A;
                padding-bottom: 20px;
                margin-bottom: 30px;
            }}
            
            .logo-title {{
                color: #1E3A8A;
                font-size: 28px;
                font-weight: 700;
                margin: 0;
            }}
            
            .report-subtitle {{
                font-size: 14px;
                color: #6B7280;
                margin: 5px 0 0 0;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            
            .meta-info {{
                text-align: right;
                font-size: 12px;
                color: #6B7280;
            }}
            
            .section-title {{
                font-size: 18px;
                color: #1E3A8A;
                border-left: 4px solid #1E3A8A;
                padding-left: 10px;
                margin-top: 30px;
                margin-bottom: 15px;
                font-weight: 600;
            }}
            
            /* Grid de KPIs */
            .kpi-container {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-bottom: 30px;
            }}
            
            .kpi-card {{
                background-color: #F3F4F6;
                padding: 15px;
                border-radius: 8px;
                text-align: center;
                border: 1px solid #E5E7EB;
            }}
            
            .kpi-val {{
                font-size: 20px;
                font-weight: 700;
                color: #111827;
                margin: 5px 0 0 0;
            }}
            
            .kpi-lbl {{
                font-size: 11px;
                color: #6B7280;
                text-transform: uppercase;
                font-weight: 500;
            }}
            
            /* Tabelas formatadas */
            .table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 25px;
                font-size: 13px;
                text-align: left;
            }}
            
            .table th {{
                background-color: #F3F4F6;
                color: #374151;
                font-weight: 600;
                padding: 10px 12px;
                border-bottom: 2px solid #E5E7EB;
            }}
            
            .table td {{
                padding: 10px 12px;
                border-bottom: 1px solid #F3F4F6;
                color: #4B5563;
            }}
            
            /* Status Badges */
            .badge {{
                font-size: 10px;
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 9999px;
                text-transform: uppercase;
            }}
            
            .badge-success {{
                background-color: #DEF7EC;
                color: #03543F;
            }}
            
            .badge-warning {{
                background-color: #FEF08A;
                color: #713F12;
            }}
            
            .badge-danger {{
                background-color: #FDE8E8;
                color: #9B1C1C;
            }}
            
            /* Rodapé de assinatura */
            .footer-signature {{
                margin-top: 50px;
                width: 100%;
                font-size: 13px;
            }}
            
            .sig-line {{
                border-top: 1px solid #9CA3AF;
                width: 250px;
                margin-top: 40px;
                padding-top: 5px;
                text-align: center;
                color: #4B5563;
            }}
            
            /* Estilos específicos para Impressão PDF */
            @media print {{
                body {{
                    background-color: #FFFFFF;
                    padding: 0;
                    font-size: 12px;
                }}
                
                .report-container {{
                    border: none;
                    box-shadow: none;
                    padding: 0;
                    max-width: 100%;
                }}
                
                .no-print {{
                    display: none;
                }}
                
                .page-break {{
                    page-break-before: always;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="report-container">
            <!-- Cabeçalho -->
            <table class="header-table" style="border-collapse: collapse;">
                <tr>
                    <td>
                        <h1 class="logo-title">📦 WMS 5.0</h1>
                        <p class="report-subtitle">Relatório Executivo de Auditoria & Estoque</p>
                    </td>
                    <td class="meta-info">
                        <strong>Emissão:</strong> {data_geracao}<br>
                        <strong>Abrangência:</strong> Almoxarifado Interno
                    </td>
                </tr>
            </table>
            
            <!-- KPIs -->
            <div class="kpi-container">
                <div class="kpi-card">
                    <div class="kpi-lbl">Insumos Cadastrados</div>
                    <div class="kpi-val">{total_itens}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-lbl">Valuation de Ativo</div>
                    <div class="kpi-val">R$ {valor_patrimonio:,.2f}</div>
                </div>
                <div class="kpi-card" style="border-left: 4px solid #E02424;">
                    <div class="kpi-lbl">Itens em Ruptura</div>
                    <div class="kpi-val" style="color: #9B1C1C;">{rupturas}</div>
                </div>
                <div class="kpi-card" style="border-left: 4px solid #D97706;">
                    <div class="kpi-lbl">Estoque Crítico</div>
                    <div class="kpi-val" style="color: #B45309;">{criticos}</div>
                </div>
            </div>
            
            <!-- Seção 1: Inventário -->
            <div class="section-title">1. Posição Consolidada de Estoque</div>
            {tb_estoque_html}
            
            <div class="page-break"></div>
            
            <!-- Seção 2: Movimentações -->
            <div class="section-title" style="margin-top: 40px;">2. Extrato Recente de Movimentações (Últimas 10)</div>
            {tb_mov_html}
            
            <!-- Assinatura -->
            <table class="footer-signature">
                <tr>
                    <td style="vertical-align: bottom;">
                        <div class="sig-line">
                            Responsável Técnico Almoxarifado
                        </div>
                    </td>
                    <td style="text-align: right; font-size: 11px; color: #9CA3AF; vertical-align: bottom;">
                        Gerado eletronicamente via WMS 5.0 - Auditoria Operacional
                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
    """
    return html_content
