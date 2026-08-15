# Jarvis WhatsApp — runbook do núcleo operacional

Estado deste documento: implementação local verificada. Publicação Meta,
variáveis do Vercel e Supabase de produção precisam de verificação no ambiente
real antes de liberar pilotos.

## O que esta versão entrega

- vínculo de um número por conta Jarvis com código de seis dígitos;
- assinatura HMAC obrigatória em todo webhook recebido da Meta;
- telefone guardado no Supabase Vault, nunca em claro nas tabelas Life;
- isolamento por usuário, recibo idempotente e conversa com memória persistida;
- texto, botões de resposta, listas, áudio/documento normalizados e recibos de
  entrega;
- outbox durável com claim atômico, retry exponencial e limite de tentativas;
- resposta contextual: “marque amanhã às 15h” + “Marketing” conserva a intenção;
- confirmação/cancelamento por botão com execução idempotente e bloqueio de
  pagamento fora do app autenticado;
- briefing diário opt-in configurável por horário, dias, áreas da vida,
  assuntos de notícias e instruções pessoais;
- notícias pesquisadas no momento do briefing, descartadas quando a resposta
  não contém URLs de fonte;
- tutorial de ativação e editor do briefing em Conexões > WhatsApp.

## Configuração do servidor

Configure os nomes abaixo somente no ambiente de produção. Nunca coloque os
valores no app, no Git ou neste documento.

```text
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_GRAPH_API_VERSION (opcional; padrão atual: v25.0)
WHATSAPP_VERIFY_TOKEN
WHATSAPP_APP_SECRET
OPENJARVIS_LIFE_CHANNEL_PEPPER
OPENJARVIS_LIFE_PUBLIC_BASE_URL
POSTGRES_PRISMA_URL ou POSTGRES_URL
CRON_SECRET
OPENAI_API_KEY
OPENJARVIS_LIFE_NEWS_MODEL (opcional; padrão: gpt-5-mini)
OPENJARVIS_LIFE_GOOGLE_CLIENT_ID
OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET
OPENJARVIS_LIFE_MICROSOFT_CLIENT_ID
OPENJARVIS_LIFE_MICROSOFT_CLIENT_SECRET
OPENJARVIS_LIFE_STRAVA_CLIENT_ID
OPENJARVIS_LIFE_STRAVA_CLIENT_SECRET
```

O `OPENJARVIS_LIFE_CHANNEL_PEPPER` deve ser aleatório, exclusivo deste
deployment e estável entre deploys. Alterá-lo impede resolver vínculos já
existentes; isso exige revincular os pilotos.

## OAuth de e-mail, agenda e treino

Os três apps OAuth usam callbacks HTTPS do próprio backend. Cadastre exatamente
estas URLs no ambiente de produção:

```text
https://jarvis-life.vercel.app/v1/life/integrations/gmail/callback
https://jarvis-life.vercel.app/v1/life/integrations/google_calendar/callback
https://jarvis-life.vercel.app/v1/life/integrations/outlook/callback
https://jarvis-life.vercel.app/v1/life/integrations/strava/callback
```

- Google: habilite Gmail API e Google Calendar API; use um cliente Web com os
  escopos `gmail.readonly` e `calendar.readonly`.
- Microsoft Entra: contas organizacionais e pessoais, com `User.Read`,
  `Mail.Read`, `Calendars.Read` e `offline_access`.
- Strava: configure o domínio de callback `jarvis-life.vercel.app` e leitura de
  atividades.
- Nunca grave access/refresh tokens no Vercel ou na tabela de conexões. Depois
  do callback, o backend guarda o payload no Supabase Vault e persiste somente
  a referência opaca.

Depois de conectar, use **Sincronizar agora** em Conexões. A sincronização é
idempotente e limitada: Gmail 25 mensagens/31 dias; agendas 100 eventos;
Strava 100 atividades/90 dias. O Jarvis recebe apenas o recorte normalizado e
trata títulos e resumos externos como dados não confiáveis, nunca instruções.

## Supabase Vault

1. Confirme no painel Supabase que a extensão Vault está habilitada.
2. A conexão PostgreSQL usada pelo backend precisa executar
   `vault.create_secret`, consultar `vault.decrypted_secrets` e apagar somente
   o UUID criado pelo Jarvis.
3. Mantenha o schema `vault` fora da Data API.
4. No SQL Editor, verifique e restrinja os papéis públicos:

```sql
revoke all on table vault.secrets from anon, authenticated;
revoke all on table vault.decrypted_secrets from anon, authenticated;
```

Não crie função `SECURITY DEFINER` pública para contornar permissão. O backend
usa sua conexão privada PostgreSQL; o iPhone nunca consulta o Vault.

## Configuração Meta Cloud API

1. Use uma conta WhatsApp Business e um número dedicado ao Jarvis.
2. No app Meta, a callback é:
   `https://SEU-DOMINIO/v1/life/webhooks/whatsapp`.
3. O verify token deve ser exatamente o valor de `WHATSAPP_VERIFY_TOKEN`.
4. Assine o campo `messages` da conta WhatsApp Business.
5. Cadastre e aprove o template `jarvis_daily_briefing`, idioma `pt_BR`, com
   uma variável no corpo (`{{1}}`) e três botões quick reply, nesta ordem:
   `Prioridades`, `Treino`, `Finanças`. Os payloads opacos são enviados pelo
   backend e não devem ser escritos como texto fixo no template.
6. Mensagens respondidas dentro da conversa usam texto, botões e listas
   interativas. Briefings fora da janela usam somente o template aprovado.
7. Nunca use biblioteca de WhatsApp Web no piloto de clientes; sessão por QR é
   frágil, pode cair e contraria a operação oficial escolhida para o produto.

## Agendamento do briefing

O endpoint interno é `GET /v1/life/internal/whatsapp/briefings` e exige
`Authorization: Bearer <CRON_SECRET>`. A execução pode ser repetida: a chave
`whatsapp-briefing:AAAA-MM-DD` impede dois envios para a mesma conta no mesmo
dia e impede uma segunda compra de notícias.

No Vercel, configure um Cron para chamar essa rota. O Vercel envia o segredo
automaticamente quando a variável `CRON_SECRET` existe no projeto. O plano
Hobby executa cron diário com horário aproximado; horários por usuário com
precisão exigem uma frequência maior disponível no plano Pro, enquanto o
backend continua decidindo quem já está no horário local.

Notícias só são pesquisadas quando a pessoa ativa a seção Notícias e define ao
menos um assunto. O custo usa o mesmo teto mensal global do Jarvis. Falha de
pesquisa, falta de crédito ou ausência de fontes não bloqueia o restante do
briefing.

## Teste local sem credenciais

```bash
task_life_tmp=$(mktemp -d)
test -n "${task_life_tmp:?}" && test -d "$task_life_tmp"
OPENJARVIS_LIFE_DB="$task_life_tmp/life.db" \
  uv run pytest tests/life tests/server/test_life_routes.py \
  tests/server/test_life_integrations_routes.py \
  tests/server/test_life_whatsapp_routes.py \
  tests/server/test_webhook_routes.py tests/channels/test_whatsapp.py \
  -q --tb=short

cd frontend
npm test
npm run build
```

## Checklist do piloto real

- [ ] `GET /health` responde 200 no domínio publicado.
- [ ] O desafio GET do webhook é aceito pela Meta.
- [ ] Uma assinatura inválida retorna 403.
- [ ] Conexões > WhatsApp envia um código sem exibi-lo na resposta HTTP.
- [ ] Responder o código muda o estado para “WhatsApp ativo”.
- [ ] “O que tenho hoje?” recebe resposta no mesmo número.
- [ ] A mesma mensagem reenviada pela Meta não gera segunda resposta.
- [ ] “Marque uma reunião amanhã às 15h” seguido de “Marketing” oferece a
      proposta correta, sem mudar o assunto para marketing.
- [ ] Botões usam IDs opacos; texto do modelo não confirma ação financeira.
- [ ] Conexões > WhatsApp salva horário, dias, seções, assuntos e instruções.
- [ ] Duas chamadas do cron no mesmo dia enviam um único template e fazem uma
      única pesquisa de notícias.
- [ ] O template entregue mostra URLs de fonte nas notícias e os três atalhos
      respondem dentro da conversa.
- [ ] Recibos `delivered` e `read` aparecem sem regressão de estado.
- [ ] Reiniciar/deployar o servidor preserva login, vínculo, memória e outbox.
- [ ] Uma segunda conta não enxerga nem revoga o vínculo da primeira.
- [ ] Google, Microsoft e Strava completam o callback sem expor `code` ou token.
- [ ] **Sincronizar agora** informa a contagem real e não duplica itens.
- [ ] E-mails, eventos e atividades aparecem no contexto do Jarvis, isolados
      por conta, sem executar instruções presentes no conteúdo importado.

## Limites desta fatia

- o template e o cron estão implementados localmente, mas ainda dependem de
  aprovação no painel Meta e configuração no deployment real;
- download/transcrição de áudio e extração de comprovantes estão normalizados
  no webhook, mas os executores de mídia ainda não fazem parte deste núcleo;
- Gmail, Outlook, Google Agenda e Strava têm OAuth, cofre e sincronização
  implementados; a liberação real depende dos apps/credenciais dos provedores;
- bancos e Apple Health ainda dependem, respectivamente, de um agregador Open
  Finance contratado e da ponte HealthKit no aplicativo iOS;
- nenhuma configuração ou deploy de produção é inferido por este runbook.
