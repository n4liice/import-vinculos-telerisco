import asyncio
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from playwright.async_api import async_playwright

app = FastAPI(title="Telerisco RPA", version="1.0.0")

# ── Configuração via variáveis de ambiente ─────────────────────────────────
TELERISCO_USER = os.environ.get("TELERISCO_USER", "")
TELERISCO_PASS = os.environ.get("TELERISCO_PASS", "")

# URLs mapeadas
LOGIN_URL    = "https://vitrine.telerisco.com.br/"
KEYCLOAK_URL = "https://maasprd.telerisco.com.br"
VITRINE_URL  = "https://vitrine.telerisco.com.br/"
APP_URL      = "https://api.telerisco.com.br/telerisco/operacional/"
# ──────────────────────────────────────────────────────────────────────────


async def run_rpa(username: str, password: str) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        # ── 1. ABRE A VITRINE (dispara fluxo Keycloak se não autenticado) ──
        await page.goto(VITRINE_URL, wait_until="networkidle", timeout=30000)

        # ── 2. LOGIN (Keycloak) ────────────────────────────────────────────
        # Aguarda o campo usuário aparecer (redireciona para Keycloak)
        await page.wait_for_selector("#username", timeout=20000)

        await page.fill("#username", username)
        await page.fill("#password", password)
        await page.click("#kc-login")  # botão "Entrar" do Keycloak

        # Aguarda retorno para a Vitrine após autenticação
        await page.wait_for_url(f"{VITRINE_URL}**", timeout=30000)
        await page.wait_for_load_state("networkidle")

        # ── 3. CLICA EM "CONTROLE DE VÍNCULOS" ────────────────────────────
        await page.click('a[href="https://api.telerisco.com.br/telerisco/operacional/"]')

        # Aguarda a aplicação Angular carregar
        await page.wait_for_url(f"{APP_URL}**", timeout=30000)
        await page.wait_for_load_state("networkidle")

        # ── 4. NAVEGA PARA ABA VÍNCULOS → MOTORISTA ───────────────────────
        await page.goto(f"{APP_URL}#/vinculo", wait_until="networkidle")
        await page.wait_for_selector("input[value='MOTORISTA']", timeout=15000)
        await page.click("input[value='MOTORISTA']")
        await page.wait_for_load_state("networkidle")

        # ── 5. CONSULTAR ──────────────────────────────────────────────────
        await page.wait_for_selector("button:has-text('Consultar')", timeout=15000)
        await page.click("button:has-text('Consultar')")

        # Aguarda tabela de resultados e botão Exportar
        await page.wait_for_selector("button:has-text('Exportar')", timeout=30000)

        # ── 6. EXPORTAR → XLS ─────────────────────────────────────────────
        await page.click("button:has-text('Exportar')")

        # Modal de formato: clica XLS
        await page.wait_for_selector("button:has-text('XLS')", timeout=10000)
        await page.click("button:has-text('XLS')")

        # Aguarda confirmação de exportação emitida
        await page.wait_for_selector(".bootbox-body", timeout=20000)

        # ── 7. CLICA "SIM" → VAI PARA RELATÓRIOS ──────────────────────────
        await page.click(".bootbox-footer button:has-text('Sim')")

        # Aguarda carregar a tela de Serviços/Downloads
        await page.wait_for_url(f"**servico-download**", timeout=15000)
        await page.wait_for_load_state("networkidle")

        # ── 8. POLLING: aguarda item mais recente ficar "Finalizado" ───────
        xls_filename = None
        max_attempts = 36  # até 6 minutos (36 × 10s)

        for attempt in range(max_attempts):
            await page.wait_for_load_state("networkidle")

            item = await page.evaluate("""
                () => {
                    const el = document.querySelector('table');
                    if (!el) return null;
                    const scope = angular.element(el).scope();
                    const list = scope && scope.sortedAndPaginatedList;
                    if (!list || !list.length) return null;
                    // Pega o item mais recente (já ordenado desc por dhrInclu)
                    return list[0];
                }
            """)

            if not item:
                await asyncio.sleep(5)
                await page.reload()
                continue

            status = item.get("status", "")
            tipo   = item.get("tipoFila", "")
            cpf    = item.get("cpfUsu", "")

            if status == "Finalizado" and "XLS" in tipo:
                xls_filename = f"{cpf}.{tipo}"
                break

            if status in ("Aguardando", "Em Processamento"):
                await asyncio.sleep(10)
                await page.reload()
                continue

            raise HTTPException(500, f"Status inesperado: {status}")

        if not xls_filename:
            raise HTTPException(504, "Timeout: XLS não ficou disponível em 6 minutos.")

        # ── 9. DOWNLOAD DO ARQUIVO ─────────────────────────────────────────
        # O download é via GET: /telerisco/operacional/download?nomeArquivo=CPF.Arquivo XLS
        # Usamos expect_download + vm.download() via Angular scope

        async with page.expect_download(timeout=60000) as dl_info:
            await page.evaluate(f"""
                () => {{
                    const el = document.querySelector('table');
                    const scope = angular.element(el).scope();
                    const vm = scope.vm;
                    const fakeEvent = {{
                        preventDefault: () => {{}},
                        stopPropagation: () => {{}}
                    }};
                    vm.download({repr(xls_filename)}, fakeEvent);
                }}
            """)

        download = await dl_info.value
        save_path = f"/tmp/telerisco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
        await download.save_as(save_path)

        with open(save_path, "rb") as f:
            xls_bytes = f.read()

        await browser.close()
        return xls_bytes


# ── ENDPOINTS ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Telerisco RPA"}


@app.get(
    "/exportar-motoristas",
    summary="Executa RPA e retorna XLS de motoristas vinculados",
    response_class=Response
)
async def exportar_motoristas(
    usuario: str = Query(default=None, description="Login (sobrescreve ENV TELERISCO_USER)"),
    senha:   str = Query(default=None, description="Senha  (sobrescreve ENV TELERISCO_PASS)")
):
    user = usuario or TELERISCO_USER
    pwd  = senha   or TELERISCO_PASS

    if not user or not pwd:
        raise HTTPException(
            400,
            "Informe usuario/senha via query-param ou variáveis TELERISCO_USER / TELERISCO_PASS"
        )

    try:
        xls_bytes = await run_rpa(user, pwd)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erro no RPA: {str(e)}")

    filename = f"motoristas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
    return Response(
        content=xls_bytes,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )