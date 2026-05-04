import asyncio
import logging
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import Response, FileResponse
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rpa")

LAST_SCREENSHOT = "/tmp/last_screenshot.png"

app = FastAPI(title="Telerisco RPA", version="1.0.0")

TELERISCO_USER = os.environ.get("TELERISCO_USER", "")
TELERISCO_PASS = os.environ.get("TELERISCO_PASS", "")
API_KEY        = os.environ.get("API_KEY", "")

VITRINE_URL = "https://vitrine.telerisco.com.br/"
APP_URL     = "https://api.telerisco.com.br/telerisco/operacional/"

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(key: str = Security(_api_key_header)):
    if not API_KEY:
        return  # sem API_KEY configurada, acesso livre
    if key != API_KEY:
        raise HTTPException(401, "API Key inválida ou ausente.")


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
            log.info("ETAPA 1: Abrindo vitrine...")
            await page.goto(VITRINE_URL, wait_until="networkidle", timeout=30000)

            # ── 2. LOGIN (Keycloak) ───────────────────────────────────────────
            log.info("ETAPA 2: Aguardando formulário de login (Keycloak)...")
            await page.wait_for_selector("#username", timeout=20000)
            await page.fill("#username", username)
            await page.fill("#password", password)
            await page.click("#kc-login")
            log.info("ETAPA 2: Credenciais enviadas, aguardando redirect...")

            await page.wait_for_url(f"{VITRINE_URL}**", timeout=30000)
            await page.wait_for_load_state("networkidle")
            log.info("ETAPA 2: Login OK — na vitrine.")

            # ── 3. CONTROLE DE VÍNCULOS ───────────────────────────────────────
            log.info("ETAPA 3: Clicando em 'Controle de Vínculos'...")
            await page.click('a[href="https://api.telerisco.com.br/telerisco/operacional/"]')
            await page.wait_for_url(f"{APP_URL}**", timeout=30000)
            await page.wait_for_load_state("networkidle")
            log.info("ETAPA 3: App Angular carregado.")

            # ── 4. ABA VÍNCULOS → MOTORISTA ───────────────────────────────────
            log.info("ETAPA 4: Navegando para #/vinculo e selecionando MOTORISTA...")
            await page.goto(f"{APP_URL}#/vinculo", wait_until="networkidle")
            await page.wait_for_selector("input[value='MOTORISTA']", timeout=15000)
            await page.click("input[value='MOTORISTA']")
            await page.wait_for_load_state("networkidle")
            log.info("ETAPA 4: MOTORISTA selecionado.")

            # ── 5. CONSULTAR ──────────────────────────────────────────────────
            log.info("ETAPA 5: Clicando em 'Consultar'...")
            await page.wait_for_selector("button:has-text('Consultar')", timeout=15000)
            await page.click("button:has-text('Consultar')")
            await page.wait_for_selector("button:has-text('Exportar')", timeout=30000)
            log.info("ETAPA 5: Resultados carregados — botão Exportar visível.")

            # ── 6. EXPORTAR → XLS ─────────────────────────────────────────────
            log.info("ETAPA 6: Clicando em 'Exportar'...")
            await page.click("button:has-text('Exportar')")
            await page.wait_for_selector("button:has-text('XLS')", timeout=10000)
            log.info("ETAPA 6: Modal de formato aberto — clicando em XLS...")
            await page.click("button:has-text('XLS')")

            # ── 7. MODAL DE CONFIRMAÇÃO ───────────────────────────────────────
            log.info("ETAPA 7: Aguardando modal de confirmação...")
            try:
                await page.locator(".bootbox").wait_for(state="visible", timeout=15000)
                await asyncio.sleep(1)
                await page.screenshot(path=LAST_SCREENSHOT, full_page=True)
                log.info("ETAPA 7: Modal visível — procurando botão 'Sim'...")

                clicked = await page.evaluate("""
                    () => {
                        const containers = ['.bootbox-footer', '.modal-footer', '.bootbox', '.modal'];
                        for (const cont of containers) {
                            const el = document.querySelector(cont);
                            if (!el) continue;
                            const btns = Array.from(el.querySelectorAll('button'));
                            const sim = btns.find(b => b.textContent.trim().toLowerCase() === 'sim');
                            if (sim) { sim.click(); return 'Sim'; }
                            if (btns.length > 0) {
                                const last = btns[btns.length - 1];
                                last.click();
                                return last.textContent.trim();
                            }
                        }
                        return null;
                    }
                """)

                if not clicked:
                    modal_html = await page.locator(".bootbox").inner_html()
                    raise HTTPException(500, f"Modal visível mas sem botão. HTML: {modal_html[:800]}")

                log.info(f"ETAPA 7: Botão clicado — '{clicked}'")

            except HTTPException:
                raise
            except Exception as e:
                log.info(f"ETAPA 7: Modal não apareceu ({e}) — assumindo exportação direta.")
                await page.screenshot(path=LAST_SCREENSHOT, full_page=True)

            # ── 8. TELA DE DOWNLOADS ──────────────────────────────────────────
            log.info("ETAPA 8: Aguardando navegação para servico-download...")
            try:
                await page.wait_for_url("**servico-download**", timeout=10000)
                log.info("ETAPA 8: Redirecionado automaticamente.")
            except Exception:
                log.info("ETAPA 8: Sem redirect — navegando manualmente para servico-download...")
                await page.goto(f"{APP_URL}#/servico-download", wait_until="networkidle", timeout=15000)
            await page.wait_for_load_state("networkidle")
            log.info(f"ETAPA 8: Na tela de downloads. URL: {page.url}")

            # ── 9. POLLING: aguarda "Finalizado" ──────────────────────────────
            await page.screenshot(path=LAST_SCREENSHOT, full_page=True)
            log.info("ETAPA 9: Iniciando polling do status do XLS (máx 6 min)...")

            xls_filename = None
            last_item_debug = None
            for attempt in range(36):
                await page.wait_for_load_state("networkidle")

                item = await page.evaluate("""
                    () => {
                        const el = document.querySelector('table');
                        if (!el) return null;
                        const scope = angular.element(el).scope();
                        if (!scope) return null;
                        const list = scope.sortedAndPaginatedList
                                  || (scope.vm && scope.vm.sortedAndPaginatedList)
                                  || (scope.$ctrl && scope.$ctrl.sortedAndPaginatedList);
                        if (!list || !list.length) return null;
                        return list[0];
                    }
                """)

                if not item:
                    log.info(f"ETAPA 9: tentativa {attempt+1}/36 — tabela vazia, aguardando 5s...")
                    if attempt == 0:
                        await page.screenshot(path=LAST_SCREENSHOT, full_page=True)
                    await asyncio.sleep(5)
                    await page.reload()
                    continue

                last_item_debug = item
                status = item.get("status", "")
                tipo   = item.get("tipoFila", "")
                cpf    = item.get("cpfUsu", "")
                log.info(f"ETAPA 9: tentativa {attempt+1}/36 — status='{status}' tipo='{tipo}' cpf='{cpf}'")

                if status == "Finalizado" and "XLS" in tipo:
                    xls_filename = f"{cpf}.{tipo}"
                    log.info(f"ETAPA 9: Finalizado! Arquivo: {xls_filename}")
                    break

                if status in ("Aguardando", "Em Processamento"):
                    await asyncio.sleep(10)
                    await page.reload()
                    continue

                raise HTTPException(500, f"Status inesperado: {status}")

            if not xls_filename:
                await page.screenshot(path=LAST_SCREENSHOT, full_page=True)
                debug = f"Último item: {last_item_debug}" if last_item_debug else "Nenhum item encontrado na tabela."
                raise HTTPException(504, f"Timeout: XLS não ficou disponível. {debug}")

            # ── 10. DOWNLOAD ──────────────────────────────────────────────────
            log.info("ETAPA 10: Iniciando download do arquivo XLS...")
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

            log.info(f"ETAPA 10: Download concluído — {len(xls_bytes)} bytes.")
            return xls_bytes

        except HTTPException:
            raise
        except Exception as e:
            await page.screenshot(path=LAST_SCREENSHOT)
            log.error(f"ERRO inesperado: {e}")
            raise HTTPException(500, f"Erro no RPA: {str(e)}")
        finally:
            await browser.close()


# ── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Telerisco RPA"}


@app.get("/screenshot", summary="Retorna screenshot do último estado capturado",
         dependencies=[Security(require_api_key)])
async def screenshot():
    if not os.path.exists(LAST_SCREENSHOT):
        raise HTTPException(404, "Nenhum screenshot disponível.")
    return FileResponse(LAST_SCREENSHOT, media_type="image/png")


@app.get(
    "/exportar-motoristas",
    summary="Executa RPA e retorna XLS de motoristas vinculados",
    response_class=Response,
    dependencies=[Security(require_api_key)]
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

    log.info(f"Requisição recebida para usuário: {user}")
    xls_bytes = await run_rpa(user, pwd)

    filename = f"motoristas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xls"
    return Response(
        content=xls_bytes,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
