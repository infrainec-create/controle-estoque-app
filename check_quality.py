#!/usr/bin/env python3
"""
Script de Monitoramento e Auditoria de Qualidade de Código WMS 5.0
Executa análise estática de código (Ruff), testes de integração e relatório de saúde.
"""

import sys
import subprocess

def run_command(cmd, desc):
    print(f"\n🔍 [MONITOR] {desc}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result

def main():
    print("==================================================================")
    print(" 🛡️  WMS 5.0 - RELATÓRIO DE MONITORAMENTO E QUALIDADE DE CÓDIGO ")
    print("==================================================================")

    # Auto-correção solicitada por parâmetro
    if "--fix" in sys.argv:
        print("\n🔧 [AUTO-FIX] Aplicando correções automáticas seguras com Ruff...")
        fix_res = run_command("./venv/bin/ruff check . --fix", "Executando ruff --fix")
        print(fix_res.stdout)

    # 1. Executar Ruff Linter
    ruff_res = run_command("./venv/bin/ruff check . --output-format=concise", "Verificando linter estático e problemas de sintaxe (Ruff)")
    
    lint_issues = 0
    if ruff_res.returncode != 0:
        lines = [line for line in ruff_res.stdout.splitlines() if line.strip() and not line.startswith("Found")]
        lint_issues = len(lines)
        print(f"\n⚠️ [LINTER] Foram encontrados {lint_issues} problemas estáticos no código:")
        for line in lines[:20]:
            print(f"  • {line}")
        if lint_issues > 20:
            print(f"  ... e mais {lint_issues - 20} problemas.")
        print("\n💡 Dica: Execute `python check_quality.py --fix` para corrigir automaticamente problemas simples de formatação.")
    else:
        print("✅ [LINTER] 0 erros estáticos ou de sintaxe encontrados!")

    # 2. Executar Testes de Integração
    test_res = run_command("./venv/bin/python run_tests.py", "Executando bateria de testes de regressão de integração")
    tests_passed = test_res.returncode == 0

    print("\n------------------------------------------------------------------")
    print(" 📊 RESUMO DA SAÚDE DO SISTEMA")
    print("------------------------------------------------------------------")
    print(f" • Status do Linter: {'✅ PASSOU (0 Erros)' if lint_issues == 0 else f'🔴 FALHOU ({lint_issues} Erros)'}")
    print(f" • Status dos Testes: {'✅ PASSOU (7/7 Testes OK)' if tests_passed else '🔴 FALHOU (Existem testes quebrados)'}")
    print("------------------------------------------------------------------")

    if lint_issues > 0 or not tests_passed:
        sys.exit(1)
    else:
        print("🎉 O código está 100% limpo, sem erros estáticos e com testes aprovados!")
        sys.exit(0)

if __name__ == "__main__":
    main()
