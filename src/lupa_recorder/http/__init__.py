"""Servidor HTTP local do agente — sub-etapa 1.7 (plano §11.3).

stdlib `http.server`, não FastAPI (o texto do plano cita FastAPI, mas a superfície é
fixa — "versão final" do §11.3 — e mínima: servir arquivo com Range, gerar m3u8
sintético, verificar um HMAC, 3 JSON de diagnóstico; nenhum recurso de framework é
usado). O que cresce nas fases seguintes é o agente como *cliente* HTTP
(`sync/client.py`, auto-update, upload R2), não este servidor. Mesma disciplina do
argparse-em-vez-de-click da 1.1 — decisão registrada no `fase1-gravador-autonomo.md`.
"""
