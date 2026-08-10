# Jarvis — o app de assistente pessoal

Um app instalável no celular onde o cliente vê a vida inteira em uma tela:
finanças, treino, rotina, família e trabalho. A tela inicial é um núcleo de voz
que reage ao microfone; atrás dela, uma grade de ícones no estilo iPhone abre
cada função **no lugar**, sem trocar de tela.

Roda separado do servidor de pesquisa do OpenJarvis: sem engine de inferência,
sem telemetria, sem agent manager. Sobe em um segundo.

## Rodar agora, sem hospedar nada

```bash
./scripts/run-life.sh
```

Isso compila o PWA, sobe o servidor e imprime dois endereços — um para este
computador e um para o celular na mesma Wi-Fi.

**O que funciona assim:** springboard, os cinco apps, pagar conta, marcar
treino e hábito, e o assistente respondendo por texto.

**O que não funciona:** voz e "adicionar à tela de início". Navegadores tratam
`http://192.168.x.x` como origem insegura e recusam o microfone e o service
worker. Isso não é limitação do app — é regra do navegador, e some assim que
existir HTTPS.

## Publicar com HTTPS

O PWA só é um *app* de verdade sobre HTTPS: é o que libera o microfone, o
service worker e o "adicionar à tela de início".

### Fly.io

O caminho mais curto. Roda container com estado, dá volume persistente para o
banco e emite HTTPS num domínio público automaticamente.

```bash
fly launch --no-deploy --copy-config --config deploy/fly/fly.toml
fly volumes create life_data --size 1 --region gru
fly deploy --config deploy/fly/fly.toml
```

`gru` é São Paulo — mantém o dado e a latência no país dos clientes. Escolha
outro nome em `app = ` no `fly.toml`: ele vira `<nome>.fly.dev` e nomes são
globais.

### Docker em qualquer VPS

```bash
cd deploy/docker
docker compose -f docker-compose.life.yml up -d --build
```

Sobe em `:8100`. Coloque um proxy reverso com TLS na frente (Caddy resolve com
duas linhas) — sem HTTPS o app funciona, mas sem voz e sem instalação.

## Primeiro acesso

Abra `/vida` no celular e crie sua conta. **A primeira conta num banco vazio é
a única que se cadastra sozinha** — depois dela o cadastro fecha, e quem achar
a URL não consegue se registrar. Para abrir um beta, defina
`OPENJARVIS_LIFE_OPEN_SIGNUP=1`.

## Variáveis

| Variável | Padrão | Para quê |
|---|---|---|
| `OPENJARVIS_LIFE_DB` | `~/.openjarvis/life.db` | Onde fica o banco |
| `OPENJARVIS_LIFE_HOST` | `127.0.0.1` | Interface. `0.0.0.0` para expor |
| `OPENJARVIS_LIFE_PORT` | `8100` | Porta |
| `OPENJARVIS_LIFE_OPEN_SIGNUP` | *(vazio)* | `1` abre o cadastro |

## O banco é a vida do cliente

`life.db` guarda saldo, boletos, treinos e nomes de familiares. Duas coisas
seguem valendo:

- **Volume, nunca camada de imagem.** O compose e o `fly.toml` já montam
  `/data`; um redeploy não pode apagar o histórico.
- **Backup é seu.** Um `sqlite3 life.db .dump` periódico é o mínimo. O arquivo
  ainda não é criptografado em repouso — se for hospedar dado financeiro real
  de terceiros, resolva isso antes de vender.

## O que o cliente pode perguntar

O núcleo de voz aceita fala em português e responde em voz. Com um engine de
inferência configurado, o modelo responde com os dados do cliente no contexto;
sem engine, o app responde a partir dos próprios registros — nunca fica mudo.

> "O que vence hoje?" · "Como está meu mês?" · "E meus treinos?" ·
> "Faltou algum hábito?" · "Tem aniversário chegando?"
