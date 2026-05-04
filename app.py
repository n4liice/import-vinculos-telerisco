import asyncio
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, FileResponse
from playwright.async_api import async_playwright

LAST_SCREENSHOT = "/tmp/last_screenshot.png"

app = FastAPI(title="Telerisco RPA", version="1.0.0")

TELERISCO_USER = os.environ.get("TELERISCO_USER", "")
TELERISCO_PASS = os.environ.get("TELERISCO_PASS", "")

VITRINE_URL = "https://vitrine.telerisco.com.br/"
APP_URL     = "https://api.telerisco.com.br/telerisco/operacional/"


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

        try:
            # ── 1. VITRINE ────────────────────────────────────────────────────
            await page.goto(VITRINE_URL, wait_until="networkidle", timeout=30000)

            # ── 2. LOGIN (Keycloak) ───────────────────────────────────────────
            await page.wait_for_selector("#username", timeout=20000)
            await page.fill("#username", username)
            await page.fill("#password", password)
            await page.click("#kc-login")

            await page.wait_for_url(f"{VITRINE_URL}**", timeout=30000)
            await page.wait_for_load_state("networkidle")

            # ── 3. CONTROLE DE VÍNCULOS ───────────────────────────────────────
            await page.click('a[href="https://api.telerisco.com.br/telerisco/operacional/"]')
            await page.wait_for_url(f"{APP_URL}**", timeout=30000)
            await page.wait_for_load_state("networkidle")

            # ── 4. ABA VÍNCULOS → MOTORISTA ───────────────────────────────────
            await page.goto(f"{APP_URL}#/vinculo", wait_until="networkidle")
            await page.wait_for_selector("input[value='MOTORISTA']", timeout=15000)
            await page.click("input[value='MOTORISTA']")
            await page.wait_for_load_state("networkidle")

            # ── 5. CONSULTAR ──────────────────────────────────────────────────
            await page.wait_for_selector("button:has-text('Consultar')", timeout=15000)
            await page.click("button:has-text('Consultar')")
            await page.wait_for_selector("button:has-text('Exportar')", timeout=30000)

            # ── 6. EXPORTAR → XLS ─────────────────────────────────────────────
            await page.click("button:has-text('Exportar')")
            await page.wait_for_selector("button:has-text('XLS')", timeout=10000)
            await page.click("button:has-text('XLS')")

            # ── 7. MODAL DE CONFIRMAÇÃO ───────────────────────────────────────
            try:
                await page.locator(".bootbox").wait_for(state="visible", timeout=15000)
                await asyncio.sleep(1)
                await page.screenshot(path=LAST_SCREENSHOT, full_page=True)

                # Tenta clique via JavaScript direto (ignora seletor CSS)
                clicked = await page.evaluate("""
                    () => {
                        const selectors = [
                            '.bootbox-footer button',
                            '.modal-footer button',
                            '.bootbox button',
                            '.modal button.btn-primary',
                            '.modal button',
                        ];
                        for (const sel of selectors) {
                            const btn = document.querySelector(sel);
                            if (btn) { btn.click(); return btn.textContent.trim(); }
                        }
                        return null;
                    }
                """)

                if not clicked:
                    modal_html = await page.locator(".bootbox").inner_html()
                    raise HTTPException(500, f"Modal visível mas sem botão. HTML: {modal_html[:800]}")

            except HTTPException:
                raise
            except Exception:
                # Sem modal visível: exportação pode ter sido enfileirada diretamente
                await page.screenshot(path=LAST_SCREENSHOT, full_page=True)

            # ── 8. TELA DE DOWNLOADS ──────────────────────────────────────────
            await page.wait_for_url("**servico-download**", timeout=15000)
            await page.wait_for_load_state("networkidle")

            # ── 9. POLLING: aguarda "Finalizado" ──────────────────────────────
            xls_filename = None
            for _ in range(36):
                await page.wait_for_load_state("networkidle")

                item = await page.evaluate("""
                    () => {
                        const el = document.querySelector('table');
                        if (!el) return null;
                        const scope = angular.element(el).scope();
                        const list = scope && scope.sortedAndPaginatedList;
                        if (!list || !list.length) return null;
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

            # ── 10. DOWNLOAD ──────────────────────────────────────────────────
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

            return xls_bytes

        except HTTPException:
            raise
        except Exception as e:
            await page.screenshot(path=LAST_SCREENSHOT)
            raise HTTPException(500, f"Erro no RPA: {str(e)}")
        finally:
            await browser.close()


# ── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Telerisco RPA"}


@app.get("/screenshot", summary="Retorna screenshot do último erro")
async def screenshot():
    if not os.path.exists(LAST_SCREENSHOT):
        raise HTTPException(404, "Nenhum screenshot disponível.")
    return FileResponse(LAST_SCREENSHOT, media_type="image/png")


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

    xls_bytes = await run_rpa(user, pwd)

    filename = f"motoristas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
    return Response(
        content=xls_bytes,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
