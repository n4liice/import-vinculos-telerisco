# Telerisco RPA — Documentação da API

Serviço RPA que automatiza a exportação de vínculos de motoristas do sistema Telerisco via Playwright (browser headless), expondo os dados como arquivo XLS via HTTP.

---

## Visão Geral

| Item | Valor |
|---|---|
| Base URL | `https://automacao-import-vinculos-telerisco.zqpvje.easypanel.host` |
| Autenticação | API Key via header `X-API-Key` |
| Formato de resposta | `application/vnd.ms-excel` (arquivo `.xls`) |
| Tempo médio de execução | 1 a 3 minutos |

---

## Autenticação

Todos os endpoints (exceto `/health`) exigem o header:

```
X-API-Key: SUA_CHAVE
```

Sem a chave ou com chave inválida, a API retorna `401 Unauthorized`.

---

## Endpoints

### `GET /health`

Verifica se o serviço está no ar. Não requer autenticação.

**Resposta:**
```json
{"status": "ok", "service": "Telerisco RPA"}
```

---

### `GET /exportar-motoristas`

Executa o RPA completo e retorna o arquivo XLS com todos os motoristas vinculados.

**Autenticação:** obrigatória (`X-API-Key`)

**Query params opcionais:**

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `usuario` | string | Login do Telerisco. Sobrescreve a variável de ambiente `TELERISCO_USER` |
| `senha` | string | Senha do Telerisco. Sobrescreve a variável de ambiente `TELERISCO_PASS` |

Se não informados, usa as variáveis de ambiente configuradas no EasyPanel.

**Resposta de sucesso (`200`):**
- Content-Type: `application/vnd.ms-excel`
- Body: arquivo binário `.xls`
- Header: `Content-Disposition: attachment; filename="motoristas_YYYYMMDD_HHMMSS.xls"`

**Respostas de erro:**

| Código | Descrição |
|---|---|
| `400` | Usuário/senha não informados e variáveis de ambiente não configuradas |
| `401` | API Key ausente ou inválida |
| `500` | Erro durante a execução do RPA (seletor não encontrado, falha de navegação, etc.) |
| `504` | Timeout — o arquivo XLS não ficou disponível em 6 minutos |

**Exemplo de chamada (curl):**
```bash
curl -X GET \
  "https://automacao-import-vinculos-telerisco.zqpvje.easypanel.host/exportar-motoristas" \
  -H "X-API-Key: SUA_CHAVE" \
  --output motoristas.xls
```

---

### `GET /screenshot`

Retorna uma imagem PNG do último estado capturado pelo browser durante a execução. Útil para depurar erros.

**Autenticação:** obrigatória (`X-API-Key`)

**Resposta de sucesso (`200`):**
- Content-Type: `image/png`

**Resposta de erro (`404`):** nenhum screenshot disponível ainda.

**Exemplo:**
```
https://automacao-import-vinculos-telerisco.zqpvje.easypanel.host/screenshot
```

---

## Fluxo interno do RPA

```
1. Abre vitrine.telerisco.com.br
2. Login via Keycloak (SSO) com usuário e senha
3. Clica em "Controle de Vínculos"
4. Navega para aba Vínculos → seleciona MOTORISTA
5. Clica em "Consultar"
6. Clica em "Exportar" → seleciona formato XLS
7. Confirma modal ("Sim")
8. Navega para tela de Relatórios/Downloads
9. Polling a cada 10s até status = "Finalizado" (máx 6 min)
10. Faz download do arquivo e retorna como resposta HTTP
```

---

## Configuração no EasyPanel

Variáveis de ambiente necessárias:

| Variável | Descrição |
|---|---|
| `TELERISCO_USER` | Login do sistema Telerisco |
| `TELERISCO_PASS` | Senha do sistema Telerisco |
| `API_KEY` | Chave de autenticação da API |

---

## Integração com n8n

Configure um nó **HTTP Request** com:

| Campo | Valor |
|---|---|
| Method | `GET` |
| URL | `https://automacao-import-vinculos-telerisco.zqpvje.easypanel.host/exportar-motoristas` |
| Response Format | `File` |
| Header | `X-API-Key: SUA_CHAVE` |
| Timeout | `300000` ms (5 minutos) |

O arquivo `.xls` retornado pode ser conectado diretamente ao próximo nó para processamento ou armazenamento.

---

## Infraestrutura

| Item | Detalhe |
|---|---|
| Hospedagem | EasyPanel |
| Container | Docker — `mcr.microsoft.com/playwright/python:v1.44.0-jammy` |
| Framework | FastAPI + Uvicorn |
| Browser | Chromium headless via Playwright |
| Repositório | `github.com/n4liice/import-vinculos-telerisco` |
| Deploy | Automático via push na branch `master` |
