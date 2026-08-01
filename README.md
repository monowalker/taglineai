# 商品キャッチフレーズ抽出

商品ページの URL を入力すると、**そのページの HTML 内に実在する文章だけ**を選び出して
「商品名 / キャッチフレーズ 1 / キャッチフレーズ 2」の 3 行を返す Web アプリです。

ローカル LLM (OpenAI 互換 API) を使いますが、**LLM は文章を生成しません**。
LLM の役割は「本文から 2 文を選ぶ」ことだけで、その出力はバックエンドで
必ず原文と突き合わせて検証されます (後述の [原文一致保証](#原文一致保証))。

抽出の**観点**を指定したり、良い結果を **👍 でお手本として貯める**こともできます
(後述の [観点 (ペルソナ) とお手本](#観点-ペルソナ-とお手本))。

---

## 目次

- [クイックスタート](#クイックスタート)
- [構成](#構成)
- [処理の流れ](#処理の流れ)
- [原文一致保証](#原文一致保証)
- [観点 (ペルソナ) とお手本](#観点-ペルソナ-とお手本)
- [モデルの切り替え](#モデルの切り替え)
- [環境変数](#環境変数)
- [LLM の設定](#llm-の設定)
- [API 仕様](#api-仕様)
- [開発方法](#開発方法)
- [テスト](#テスト)
- [ログ](#ログ)
- [トラブルシューティング](#トラブルシューティング)

---

## クイックスタート

前提: **Docker と Docker Compose だけ**。Node.js や Python をホストに入れる必要はありません。

```bash
cd /home/shared-docker/taglineai

# 使用するモデルの定義を用意する (初回のみ)
cp models.example.yml models.yml

docker compose up --build
```

起動後、ブラウザで **<http://localhost:8090>** を開きます。

> `models.yml` は **API キーを平文で書けるように `.gitignore` 済み**です。
> そのため clone 直後には存在しません。`models.example.yml` をコピーしてから編集してください。
> (コピーし忘れても起動は失敗せず、`.env` の `LLM_*` から 1 件だけ作って動きます)

停止:

```bash
docker compose down
```

> **ローカル LLM は別途起動しておく必要があります。**
> 既定では同一ホストの `http://localhost:8080/v1` (OpenAI 互換) を見に行きます。
> 例: `cd /home/shared-docker/llama_server && docker compose up -d`

### 使い方

1. テキストエリアに URL を **改行区切り** で入力する

   ```
   https://aaa.com
   https://bbb.com
   https://ccc.com
   ```

2. **抽出開始** を押す
3. URL ごとに「商品名 / キャッチフレーズ 1 / キャッチフレーズ 2」が表示される

   ```
   感謝をコメて 魚沼産コシヒカリ

   新潟県魚沼産コシヒカリを使用。

   米の宝石と呼ばれるブレンド米です。
   ```

失敗した URL は、その URL のカードにだけエラーが表示されます。他の URL の処理は継続します。

### 画面でできること

| 操作 | 説明 |
|---|---|
| **クリア** | URL 入力欄を空にする |
| **モデル** (右上) | 使用する LLM を切り替える ([モデルの切り替え](#モデルの切り替え)) |
| **設定** (右上) | 抽出の観点とお手本を編集する ([観点とお手本](#観点-ペルソナ-とお手本)) |
| **⧉ コピー** | 商品名 / キャッチフレーズを 1 行ずつコピー。カード右上の「コピー」は 3 行まとめて |
| **候補文: N** | クリックすると、**LLM に渡した候補文の一覧**が開く。採用された文には ★ が付く |
| **👍** | その結果をお手本として登録し、次回以降の抽出の参考にする |

「候補文」は、なぜその文が選ばれたのか (あるいは選ばれなかったのか) を確認するのに使えます。
期待した文が一覧に無ければ前処理で落ちている、一覧にあるのに選ばれていなければ LLM の判断、
と切り分けられます。

---

## 構成

```
taglineai/
├── docker-compose.yml      # frontend / backend / nginx の 3 コンテナ
├── .env                    # 全設定 (ポート・タイムアウト・APIキー等)
├── models.yml              # 使用できる LLM の一覧 (プルダウンの中身 / gitignore 済み)
├── models.example.yml      # ↑ の雛形 (キーを書かないこと)
├── README.md
│
├── backend/                # FastAPI (Python 3.12)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py             # アプリ生成・ミドルウェア・例外ハンドラ
│   │   ├── schemas.py          # リクエスト / レスポンスの型
│   │   ├── core/
│   │   │   ├── config.py       # 環境変数 → 設定
│   │   │   ├── logging.py      # ログ設定 (リクエスト ID 付き)
│   │   │   └── errors.py       # ドメイン例外
│   │   ├── api/
│   │   │   ├── routes.py       # エンドポイント定義
│   │   │   └── deps.py         # 依存性注入
│   │   └── services/
│   │       ├── fetcher.py      # HTML 取得 (requests / 文字コード判定)
│   │       ├── html_parser.py  # 商品名・本文抽出 (trafilatura → readability → bs4)
│   │       ├── preprocess.py   # 改行/空白整理・文分割・重複削除・ノイズ除去
│   │       ├── llm.py          # OpenAI 互換クライアント / プロンプト組み立て
│   │       ├── verifier.py     # ★ 原文一致検証
│   │       ├── selector.py     # 検証失敗時のフォールバック選択
│   │       ├── model_registry.py # models.yml の読み込みとモデル別クライアント
│   │       ├── store.py        # 観点プリセット・お手本・選択モデルの永続化 (JSON)
│   │       └── pipeline.py     # 全体のオーケストレーション
│   └── tests/                  # pytest
│
├── frontend/               # React 19 + Vite + TypeScript + Bootstrap 5
│   ├── Dockerfile          # ビルド → nginx で静的配信
│   ├── nginx.conf
│   ├── package.json
│   └── src/
│       ├── App.tsx
│       ├── api/            # API クライアントと型
│       ├── components/     # UrlForm / ResultList / ResultCard / CopyButton /
│       │                   # ModelSelect / SettingsModal / LoadingPanel
│       ├── utils/          # URL 解析・クリップボード・お手本の同一性判定
│       └── styles/
│
└── nginx/                  # 公開エッジ (リバースプロキシ)
    ├── Dockerfile
    └── default.conf
```

### コンテナ

| サービス | 役割 | 公開 |
|---|---|---|
| `nginx` | 公開エッジ。`/` → frontend、`/api/` → backend | `${APP_PORT}` (既定 8090) |
| `frontend` | React のビルド成果物を静的配信 | 内部のみ |
| `backend` | FastAPI + uvicorn | 内部のみ |

ブラウザから見えるオリジンは 1 つだけなので、CORS の設定は不要です。

nginx は Docker 組み込み DNS でリクエストごとに名前解決します
(`proxy_pass` に変数を使用)。`upstream` ブロックは**起動時に一度だけ**名前解決するため、
`docker compose up -d --build backend` でコンテナを作り直すと IP が変わり、
nginx を再起動するまで 502 が出続けます。それを避けるための構成です。

観点プリセットと 👍 のお手本は、named volume `taglineai_data` の `/data/store.json` に
保存されます。`docker compose down` してもコンテナを作り直しても残ります
(消したい場合は `docker compose down -v`)。

---

## 処理の流れ

```
URL
 └─▶ ① HTML 取得        requests / タイムアウト 15 秒 / UA 設定 / 文字コード自動判定
      └─▶ ② HTML 解析    trafilatura → readability-lxml → BeautifulSoup の順にフォールバック
           │             広告・ヘッダ・フッタ・サイドバー・script・style・コメントを除去
           │             商品名は JSON-LD → og:title → title → h1 の優先順で取得
           └─▶ ③ 前処理   HTML タグ除去 / 改行整理 / 空白整理 / 文分割 /
                │        長文分割 / 重複削除 / ナビ等のノイズ除去 → 候補文リスト
                └─▶ ④ LLM  商品名・候補文・観点・お手本を送信 (HTML は送らない)
                     │      「生成禁止・選択のみ」のシステムプロンプト
                     └─▶ ⑤ 原文一致検証 ← ★ここが本質
                          └─▶ ⑥ 画面表示
```

---

## 原文一致保証

LLM に「生成するな」と指示するだけでは、要約・言い換え・創作を完全には防げません。
そこで本アプリは **LLM の出力をそのまま採用しません**。
`backend/app/services/verifier.py` が LLM の各行を元ページのテキストに突き合わせ、
以下のいずれかに分類します。

| 判定 | 内容 | 採用する文字列 | 画面のバッジ |
|---|---|---|---|
| `exact` | 候補文と完全一致 | 候補文そのまま | 原文一致 |
| `contained` | 元テキストの部分文字列 | **元テキストから切り出した原文** | 原文一致 |
| `fuzzy` | 候補文と高い類似度 (既定 `VERIFY_MIN_RATIO` = 0.75 以上) | **類似した候補文の原文**（LLM の文は捨てる） | 原文に補正 |
| `nearest` | 検証は通らないが、言い換え元らしき候補がある (既定 `FALLBACK_MIN_RATIO` = 0.5 以上) | **最も近い候補文の原文** | 近い原文に置換 |
| `unverified` | 言い換え元も無い = 完全な創作 | **候補文からスコア順に選び直す** | 本文から自動選択 |

比較は NFKC 正規化 + 空白除去 + 小文字化で行い、全角/半角や空白の揺れを吸収します。
**正規化は比較にしか使わず、返す文字列は常に原文のまま**です。

### 棄却されたときのフォールバック

LLM が原文を言い換えたときは、**言い換え元の文が候補の中にあることがほとんど**です。
そこで、いきなりスコア順に選び直すのではなく、まず「LLM が狙っていた文」を探します。

```
LLM: 多彩な味わいが楽しめる、贅沢なカレーとスープのセットです。   ← 言い換え (類似度 0.68)

  ✗ いきなりスコア順   → おすすめのギフトシーン・贈る相手        （無関係な見出し）
  ○ 最も近い候補を探す → 多彩な味わいが楽しめる人気カレーと
                          スープの詰め合わせ！                    （言い換え元）
```

完全な創作（似た候補が 1 つも無い）の場合だけ、スコア順のヒューリスティック
(`selector.py`) に落ちます。

**どの経路をたどっても、返るのは必ず候補文か原文の切り出しです。**
LLM が生成した文字列がそのまま画面に出ることはありません
(`tests/test_pipeline.py::TestVerbatimEnforcement::test_the_fallback_never_returns_llm_text`)。

また `line2` と `line3` が同一になることは禁止されており、検証後に一致した場合は
`line3` を選び直します。

### 商品名の決め方

次の優先順位で決定し、LLM の出力より HTML の値を優先します。

| 優先 | 取得元 | `meta.title_source` |
|---|---|---|
| 0 | 構造化データ schema.org `Product.name` (JSON-LD) | `json-ld` |
| 1 | `<meta property="og:title">` | `og:title` |
| 2 | `<title>` | `title` |
| 3 | `<h1>` | `h1` |

**0 を先頭に置いているのは、EC サイトの `og:title` / `<title>` が検索対策で
「商品名 + キャッチ + サイト名」になっていることが多いため**です。例えば

```
og:title  NISHIKIYA KITCHEN カレー＆スープ6個ギフトのギフト通販
          - 贈り物、ギフトの専門店 PRESENTERS ROOM
JSON-LD   NISHIKIYA KITCHEN / カレー＆スープ6個ギフト     ← 商品名そのもの
```

JSON-LD の `Product.name` は Google の商品リッチリザルト対応で広く実装されており、
商品名そのものが入ります。`@graph` や配列、`http://schema.org/Product` 形式の
`@type` にも対応し、`Brand` や `Offer` の `name` を誤って拾うことはありません。
壊れた JSON-LD を置いているサイトでは黙って読み飛ばし、1〜3 の従来どおりの順序で決まります。

いずれの取得元でも、行う加工は空白の整理だけで**文字は書き換えません**。

---

## 観点 (ペルソナ) とお手本

画面右上の **設定** から操作します。

### 観点 (プロンプト)

「どんな文を選んでほしいか」を自由入力で指定します (例: `明るく前向きな印象の文を選んでください。`)。

- **プリセットとして 5 件まで保存**でき、一覧から「使う」で呼び出せます
- 保存せずにその場で入力した内容でも抽出できます
- 保存済みのプリセットは削除できます
- 空にすると観点なしで抽出します

観点はシステムプロンプトの `【選ぶ際の観点】` として渡されますが、
**「どの文を選ぶか」の判断にのみ使う**ことを明示しており、
生成・書き換え禁止のルールは緩めません。観点を付けても出力は
[原文一致検証](#原文一致保証)を通ります。

> **現在の観点はサーバ側に 1 つだけ保持されます。**
> 設定ダイアログを「閉じる」ときにその内容が保存されるため、
> 複数のタブで同時に設定を開いていると、**後から閉じたタブの内容で上書き**されます。
> 抽出リクエスト自体は画面に表示されている観点をそのまま送るので、
> 抽出結果が意図しない観点で行われることはありません。

### お手本 (👍)

結果カードの **👍** を押すと、その抽出結果がお手本として保存され、
次回以降の抽出でシステムプロンプトの `【お手本】` として LLM に渡されます。

- **直近 10 件**だけが保持され、超えると古いものから自動的に消えます
- もう一度 👍 を押すと解除されます
- 仕様書の出力例が「組み込みのお手本」として最初から入っています (削除不可)
- お手本の文章がそのまま出力に混入しないよう、プロンプトで明示的に禁じたうえ、
  最終的には原文一致検証で弾かれます

---

## モデルの切り替え

`models.yml` に使用できる LLM を並べておくと、**画面右上のプルダウンで切り替え**られます。
OpenAI 互換 API を喋るものなら何でも登録できます (ローカル LLM / Claude / Gemini / OpenAI …)。

```yaml
default: local          # 起動時に選択されているモデル

models:
  - id: local
    label: ローカルLLM (Ornith 35B)      # プルダウンに出る名前
    api_base: http://host.docker.internal:8080/v1
    model: Ornith-1.0-35B-UD-IQ4_NL.gguf
    api_key: ""
    concurrency: 1      # llama.cpp の --parallel に合わせる
    timeout: 180        # ローカルは遅いので長め

  - id: claude
    label: Claude Sonnet 5
    api_base: https://api.anthropic.com/v1
    model: claude-sonnet-5
    api_key: "${ANTHROPIC_API_KEY}"
    concurrency: 4      # クラウドは並列に強い
    timeout: 60
```

編集後は **`docker compose restart backend`** で反映されます (再ビルド不要)。

> `model` にはプロバイダが定めた**モデルID**を書きます。表示名ではありません。
> ```
> ○ model: gemini-3.5-flash-lite
> × model: Gemini 3.5 Flash Lite   → 400 unexpected model name format
> ```
> 使えるIDは `curl -H "Authorization: Bearer $KEY" <api_base>/models` で確認できます。

| 項目 | 必須 | 説明 |
|---|---|---|
| `id` | ○ | 内部の識別子。API やログに出る |
| `api_base` | ○ | OpenAI 互換エンドポイント |
| `model` | ○ | サーバに送るモデル名 |
| `label` | — | プルダウンの表示名 (既定: `id`) |
| `api_key` | — | 直書き、`${ENV_VAR}` 参照、空 (キー不要) のいずれか |
| `concurrency` | — | 同時リクエスト数 (既定: `LLM_CONCURRENCY`) |
| `timeout` | — | タイムアウト秒 (既定: `LLM_TIMEOUT`) |
| `temperature` | — | 既定: `LLM_TEMPERATURE` |
| `max_tokens` | — | 既定: `LLM_MAX_TOKENS` |

`concurrency` と `timeout` をモデルごとに持てるのが要点で、
「ローカルは 1 並列で 3 分、クラウドは 4 並列で 1 分」のように実態に合わせられます。

### API キーの扱い

**API キーがフロントエンドに渡ることはありません。** `GET /api/models` が返すのは
`id` / `label` / `api_base` / `model` / **キーの有無** だけで、値そのものは含みません
(`tests/test_model_registry.py::TestRegistry::test_listed_info_never_contains_the_api_key`)。

**`models.yml` は `.gitignore` 済み**なので、キーを直書きしても差し支えありません。
それでも `${GEMINI_API_KEY}` のように環境変数参照にして `.env` に置くほうが、
設定ファイルを共有しやすく安全です (`docker-compose.yml` 経由でコンテナに渡ります)。

雛形の `models.example.yml` はリポジトリに含まれるので、**こちらにはキーを書かないでください。**

参照している環境変数が未設定の場合は、プルダウンに「APIキー未設定」と表示され、
起動ログにも警告が出ます。

### 選択の優先順位

```
1. リクエストの model_id      … POST /api/extract の model_id
2. 画面で選択したモデル        … サーバに永続化される
3. models.yml の default
4. 環境変数 LLM_API_BASE 等   … models.yml が読めない場合のみ
```

未知の `id` を指定した場合は、警告ログを出して既定のモデルにフォールバックします。

### ファイルが無い / 壊れている場合

**起動は失敗しません。** `models.yml` が無い・壊れている・空の場合は、
従来どおり `LLM_API_BASE` / `LLM_MODEL` / `LLM_API_KEY` から 1 件だけ組み立てて動きます。
1 エントリだけ記述ミスがある場合は、**その行だけを飛ばして**残りを読み込みます。

---

## 環境変数

すべて `.env` に定義されています。値を変えたら `docker compose up -d` で反映されます。

### アプリ

| 変数 | 既定値 | 説明 |
|---|---|---|
| `APP_PORT` | `8090` | ブラウザからアクセスするポート |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `MAX_URLS_PER_REQUEST` | `20` | 1 リクエストで処理できる URL 数の上限 |

### LLM

> **通常は `models.yml` でモデルを定義します** ([モデルの切り替え](#モデルの切り替え))。
> 以下の `LLM_*` は、`models.yml` が読めなかった場合のフォールバックと、
> `models.yml` で省略された項目の既定値として使われます。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `MODELS_FILE` | `/app/models.yml` | モデル定義ファイルのパス |
| `LLM_API_BASE` | `http://host.docker.internal:8080/v1` | OpenAI 互換 API のベース URL |
| `LLM_MODEL` | `Ornith-1.0-35B-UD-IQ4_NL.gguf` | モデル名 |
| `LLM_API_KEY` | (空) | 不要なサーバでは空のままでよい |
| `LLM_TIMEOUT` | `180` | LLM 応答のタイムアウト秒 |
| `LLM_CONCURRENCY` | `1` | LLM への同時リクエスト数 |
| `LLM_TEMPERATURE` | `0` | 生成ではなく選択させるため 0 を推奨 |
| `LLM_MAX_TOKENS` | `1024` | 応答の上限トークン数 |

### HTML 取得

| 変数 | 既定値 | 説明 |
|---|---|---|
| `HTTP_TIMEOUT` | `15` | 取得タイムアウト秒 (仕様値) |
| `HTTP_USER_AGENT` | Chrome 相当 | 送信する User-Agent |
| `HTTP_MAX_BYTES` | `5000000` | ダウンロード上限バイト数 |
| `HTTP_MAX_REDIRECTS` | `5` | 追跡するリダイレクト回数 |
| `FETCH_CONCURRENCY` | `4` | HTML 取得の同時実行数 |
| `HTTP_ALLOW_PRIVATE_HOSTS` | `true` | プライベート IP 宛の取得可否。インターネット上のページだけを扱うなら `false` (SSRF 対策) |

### 前処理・検証

| 変数 | 既定値 | 説明 |
|---|---|---|
| `MIN_SENTENCE_CHARS` | `8` | 候補文の最小文字数 |
| `MAX_SENTENCE_CHARS` | `140` | これを超える文は分割する |
| `MAX_CANDIDATES` | `120` | LLM に渡す候補文の最大件数 |
| `MAX_BODY_CHARS` | `6000` | LLM に渡す本文の最大文字数 |
| `VERIFY_MIN_RATIO` | `0.75` | `fuzzy` 一致とみなす最小類似度 |
| `FALLBACK_MIN_RATIO` | `0.5` | 棄却時に「言い換え元」とみなす最小類似度。`VERIFY_MIN_RATIO` より低くする |

### 観点・お手本

| 変数 | 既定値 | 説明 |
|---|---|---|
| `DATA_DIR` | `/data` | 保存先ディレクトリ (named volume がマウントされる) |
| `MAX_PERSONA_PRESETS` | `5` | 保存できる観点プリセットの数 |
| `MAX_EXAMPLES` | `10` | 👍 で貯まるお手本の保持件数 |
| `MAX_PROMPT_EXAMPLES` | `6` | プロンプトに含めるお手本の最大件数 |
| `MAX_PERSONA_PROMPT_CHARS` | `400` | 観点プロンプトの最大文字数 |

---

## LLM の設定

OpenAI 互換 API であれば何でも接続できます。

### llama.cpp (llama-server) — 本リポジトリの既定

```bash
cd /home/shared-docker/llama_server
docker compose up -d
curl http://127.0.0.1:8080/health     # {"status":"ok"}
```

`.env`:

```ini
LLM_API_BASE=http://host.docker.internal:8080/v1
LLM_MODEL=Ornith-1.0-35B-UD-IQ4_NL.gguf
LLM_API_KEY=
```

`host.docker.internal` は `docker-compose.yml` の `extra_hosts: host-gateway` で
ホスト側 IP に解決されます (Linux でも動作します)。

### Ollama

```ini
LLM_API_BASE=http://host.docker.internal:11434/v1
LLM_MODEL=qwen3:8b
LLM_API_KEY=
```

### Groq

```ini
LLM_API_BASE=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=gsk_xxxxxxxx
```

推論が非常に速い一方、無料枠はレート制限が厳しめです。`429` が返る場合は
`concurrency` を下げてください。

### LM Studio

```ini
LLM_API_BASE=http://host.docker.internal:1234/v1
LLM_MODEL=your-loaded-model
LLM_API_KEY=
```

### API キーが必要なサーバ

```ini
LLM_API_KEY=sk-xxxxxxxx
```

キーが空の場合はダミー値 (`no-key`) を送ります。キー検証をしないサーバ
(llama.cpp / Ollama / LM Studio など) はこれをそのまま無視します。

### 疎通確認

```bash
curl http://localhost:8090/api/llm/ping
```

```json
{"reachable":true,"llm_api_base":"http://host.docker.internal:8080/v1","llm_model":"...","latency_ms":3.2,"detail":null}
```

### LLM に送る内容

送信するのは **商品名・抽出済みの候補文・観点・お手本だけ**です。HTML は一切送りません。
システムプロンプトは `backend/app/services/llm.py` の `build_system_prompt()` が組み立てます
(生成禁止のルールは `EXTRACTION_RULES` に固定文言で定義)。

---

## API 仕様

ベース URL: `http://localhost:8090/api`
OpenAPI ドキュメント: <http://localhost:8090/api/docs>

### `POST /api/extract`

商品ページからキャッチフレーズを抽出します。

**リクエスト**

```json
{
  "urls": [
    "https://aaa.com",
    "https://bbb.com",
    "https://ccc.com"
  ],
  "persona_prompt": "明るく前向きな印象の文を選んでください。"
}
```

| フィールド | 必須 | 説明 |
|---|---|---|
| `urls` | ○ | 対象 URL。空行は自動的に除去される |
| `model_id` | — | 使用するモデル。省略時は画面で選択中のモデル。未定義の id は既定のモデルにフォールバック |
| `persona_prompt` | — | 文を選ぶ際の観点。**省略/`null` なら保存されている観点を使い、空文字なら観点なし**で抽出する |

**レスポンス** `200 OK` — 入力した URL と同じ順序・同じ件数の配列を返します。

```json
[
  {
    "url": "https://aaa.com",
    "title": "感謝をコメて 魚沼産コシヒカリ",
    "line2": "新潟県魚沼産コシヒカリを使用。",
    "line3": "米の宝石と呼ばれるブレンド米です。",
    "error": null,
    "error_code": null,
    "meta": {
      "fetch_ms": 312.4,
      "extract_ms": 41.8,
      "llm_ms": 8421.0,
      "total_ms": 8790.2,
      "http_status": 200,
      "extractor": "trafilatura",
      "candidate_count": 34,
      "candidates": ["新潟県魚沼産コシヒカリを使用。", "..."],
      "title_source": "json-ld",
      "line2_match": "exact",
      "line3_match": "exact",
      "persona_applied": true,
      "example_count": 3,
      "llm_model": "Ornith-1.0-35B-UD-IQ4_NL.gguf",
      "llm_model_id": "local",
      "llm_model_label": "ローカルLLM (Ornith 35B)"
    }
  }
]
```

| フィールド | 説明 |
|---|---|
| `url` | 入力した URL |
| `title` | 商品名 ([決め方](#商品名の決め方)を参照) |
| `line2` | 商品の魅力を表す、原文そのままの一文 |
| `line3` | 商品の特徴を表す、原文そのままの一文 (`line2` とは必ず異なる) |
| `error` | 失敗時のメッセージ。成功時は `null` |
| `error_code` | 失敗時の種別コード |
| `meta` | 処理時間や原文一致方式などの付随情報 |

**個別 URL のエラー** — 1 件が失敗しても HTTP ステータスは 200 のままで、
該当要素にだけ `error` が入ります。

```json
[
  {
    "url": "https://bbb.com",
    "title": "",
    "line2": "",
    "line3": "",
    "error": "HTML の取得がタイムアウトしました (15 秒)。",
    "error_code": "fetch_timeout",
    "meta": { "total_ms": 15002.1 }
  }
]
```

| `error_code` | 意味 |
|---|---|
| `invalid_url` | URL 形式が不正 / 許可されないスキームや宛先 |
| `fetch_timeout` | HTML 取得がタイムアウト |
| `fetch_error` | 接続失敗・HTTP エラー・HTML 以外のコンテンツ |
| `content_extraction_error` | 本文を抽出できなかった |
| `llm_error` | LLM への接続・応答に失敗 |
| `llm_parse_error` | LLM 応答を JSON として解釈できなかった |
| `internal_error` | 想定外のエラー |

**リクエスト全体のエラー**

| ステータス | 条件 |
|---|---|
| `400` | URL 件数が `MAX_URLS_PER_REQUEST` を超えた |
| `422` | `urls` が無い / 空 / 空行のみ |
| `500` | サーバ内部エラー |

### モデルのエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/models` | 使用できるモデルの一覧と選択状態。**API キーは含まない** |
| `PUT` | `/api/models/selected` | 使用するモデルを選択する (`{"model_id"}`)。未定義なら `404` |

```json
// GET /api/models
{
  "models": [
    {"id": "local", "label": "ローカルLLM (Ornith 35B)",
     "api_base": "http://host.docker.internal:8080/v1",
     "model": "Ornith-1.0-35B-UD-IQ4_NL.gguf",
     "has_api_key": false, "missing_env_key": false, "concurrency": 1}
  ],
  "selected_id": "local"
}
```

### 観点 (ペルソナ) のエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/personas` | プリセット一覧・現在の観点・上限件数を返す |
| `POST` | `/api/personas` | 観点をプリセットとして保存する (`{"name","prompt"}`)。上限超過は `400` |
| `DELETE` | `/api/personas/{id}` | プリセットを削除する。存在しなければ `404` |
| `PUT` | `/api/personas/active` | 現在の観点を設定する (`{"prompt"}`)。空文字で解除 |

```json
// GET /api/personas
{
  "presets": [
    {"id": "a1b2c3", "name": "明るい", "prompt": "明るく前向きな印象の文を選んでください。",
     "created_at": "2026-08-01T00:00:00+00:00"}
  ],
  "active_prompt": "明るく前向きな印象の文を選んでください。",
  "max_presets": 5
}
```

### お手本のエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/examples` | お手本一覧 (新しい順、末尾に組み込み) と保持上限を返す |
| `POST` | `/api/examples` | お手本を追加する (`{"title","line2","line3","source_url"}`) |
| `DELETE` | `/api/examples/{id}` | お手本を削除する。組み込みは削除できず `404` |

`POST` 時に保持上限 (`MAX_EXAMPLES`) を超えると、古いものから自動的に削除されます。
同じ内容を再度追加しても重複せず、最新として並び替えられます。

### `GET /api/health`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "llm_api_base": "http://host.docker.internal:8080/v1",
  "llm_model": "...",
  "max_urls_per_request": 20
}
```

### `GET /api/llm/ping`

ローカル LLM への疎通確認。上記 [疎通確認](#疎通確認) を参照。

### curl の例

```bash
# 観点なしで抽出
curl -X POST http://localhost:8090/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://example.com/item/1"],"persona_prompt":""}' | jq

# 観点を指定して抽出
curl -X POST http://localhost:8090/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://example.com/item/1"],
       "persona_prompt":"お年寄りにも分かりやすい、やさしい言葉の文を選んでください。"}' | jq

# 観点をプリセットとして保存
curl -X POST http://localhost:8090/api/personas \
  -H 'Content-Type: application/json' \
  -d '{"name":"シニア向け","prompt":"お年寄りにも分かりやすい文を選んでください。"}' | jq
```

---

## 開発方法

### 全部 Docker で回す (推奨)

```bash
docker compose up --build          # 起動
docker compose logs -f backend     # バックエンドのログ
docker compose up -d --build backend   # backend だけ作り直す
docker compose down                # 停止
```

コードを変更したら該当サービスを `--build` で作り直してください
(本番同等の構成を保つため、ホットリロード用のバインドマウントは使っていません)。

### フロントだけホストで開発する

Vite の開発サーバを使うとホットリロードが効きます。
`/api` へのリクエストは `http://localhost:8090` (= nginx) にプロキシされるので、
バックエンドは Docker で起動したままにしてください。

```bash
docker compose up -d               # backend / nginx を起動しておく
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

### バックエンドだけホストで動かす

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export LLM_API_BASE=http://localhost:8080/v1
export LLM_MODEL=Ornith-1.0-35B-UD-IQ4_NL.gguf
uvicorn app.main:app --reload --port 8000
```

### コード構成の考え方

- `services/` の各モジュールは互いに独立しており、単体でテストできます。
- HTML 取得・解析・前処理・LLM・検証がそれぞれ 1 ファイルに閉じているので、
  抽出ロジックの差し替え (例: 別の本文抽出ライブラリ追加) は 1 箇所で済みます。
- 例外は `core/errors.py` のドメイン例外に集約し、`pipeline.py` が
  URL 単位のエラー結果へ変換します。1 件の失敗が他に波及しません。

---

## テスト

```bash
# コンテナ内で実行する (追加セットアップ不要)
docker compose run --rm backend pytest

# 詳細表示
docker compose run --rm backend pytest -v
```

ホストで実行する場合:

```bash
cd backend
pip install -r requirements.txt
pytest
```

テストの範囲:

| ファイル | 対象 |
|---|---|
| `tests/test_html_parser.py` | 商品名の優先順位 (JSON-LD 含む)、本文抽出、ノイズ除去 |
| `tests/test_preprocess.py` | 空白整理・文分割・長文分割・重複削除・ノイズ判定 |
| `tests/test_fetcher.py` | URL バリデーション、文字コード判定 |
| `tests/test_verifier.py` | **原文一致検証** (exact / contained / fuzzy / nearest / 棄却) |
| `tests/test_llm.py` | LLM 応答のパース、エラー処理、プロンプト内容 (観点・お手本) |
| `tests/test_store.py` | 観点プリセット・お手本の永続化、上限、破損ファイルの扱い |
| `tests/test_model_registry.py` | `models.yml` の読み込み、環境変数展開、**API キー非公開**、壊れた定義の扱い |
| `tests/test_pipeline.py` | パイプライン全体、原文保証、観点・お手本、エラーの局所化 |
| `tests/test_api.py` | REST API の入出力・バリデーション |

テストはネットワークに一切アクセスしません (HTML 取得と LLM はテストダブルに差し替え)。
保存領域も `tmp_path` に隔離されるため、実際のデータは汚れません。

---

## ログ

すべて標準出力に出力されます (`docker compose logs -f backend`)。

```
2026-07-31T23:58:01+0000 INFO     [a1b2c3d4e5f6] app.services.fetcher: html fetched url=https://... status=200 bytes=48211 encoding=utf-8 fetch_ms=312.4
2026-07-31T23:58:01+0000 INFO     [a1b2c3d4e5f6] app.services.pipeline: page prepared url=https://... extractor=trafilatura title_source=og:title candidates=34 extract_ms=41.8
2026-07-31T23:58:10+0000 INFO     [a1b2c3d4e5f6] app.services.llm: llm responded model=... llm_ms=8421.0 wait_ms=0.0 chars=182 persona=yes examples=3
2026-07-31T23:58:10+0000 INFO     [a1b2c3d4e5f6] app.services.pipeline: extraction succeeded url=https://... line2_match=exact line3_match=exact total_ms=8790.2
2026-07-31T23:58:10+0000 INFO     [a1b2c3d4e5f6] app.main: access method=POST path=/api/extract status=200 elapsed_ms=8792.5 client=172.20.0.4
```

| 種類 | 内容 |
|---|---|
| アクセスログ | メソッド / パス / ステータス / 所要時間 / クライアント IP |
| HTML 取得時間 | `fetch_ms` |
| LLM 応答時間 | `llm_ms` (順番待ちの時間は `wait_ms` として分離) |
| エラーログ | 取得失敗・LLM エラー・原文不一致 (`llm output rejected`) など |
| フォールバック | 言い換え元の採用 (`fell back to the nearest source sentence`)、スコア順の選び直し (`fell back to the highest scoring candidate`) |
| 保存操作 | プリセット・お手本の追加/削除、上限による切り捨て (`examples trimmed`) |

行頭の `[...]` はリクエスト ID です。レスポンスの `X-Request-ID` ヘッダと対応するので、
特定のリクエストのログだけを追えます。

```bash
docker compose logs backend | grep a1b2c3d4e5f6
```

---

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| すべての URL が `llm_error` になる | ローカル LLM が起動しているか確認 (`curl http://localhost:8080/health`)。`curl http://localhost:8090/api/llm/ping` で backend からの到達性を確認 |
| `LLM の応答がタイムアウトしました` | `.env` の `LLM_TIMEOUT` を伸ばす。モデルが大きいと 1 件に数分かかることがある |
| `内部ネットワーク宛の URL は許可されていません` | 社内 / ローカルのページを読ませたい場合は `.env` の `HTTP_ALLOW_PRIVATE_HOSTS=true` |
| 商品名にサイト名やキャッチが混ざる | そのサイトが `og:title` に検索対策の文字列を入れている。JSON-LD があれば自動的にそちらを使う。無い場合は `meta.title_source` を確認する (`og:title` なら仕様どおりの動作) |
| `商品説明として使える文章が見つかりませんでした` | JavaScript でレンダリングされるページなど、HTML に本文が無いケース。本アプリは静的 HTML のみを対象とする |
| `unexpected model name format` / `model not found` | `model` に表示名を書いている可能性。プロバイダのモデルID (小文字ハイフン区切り) を指定する |
| エラーの原因が分からない | エラーメッセージにはプロバイダが返した本文がそのまま入る。`docker compose logs backend \| grep 'llm returned an error'` でも確認できる |
| プルダウンにモデルが1件しか出ない | `models.yml` が読めていない可能性。起動ログの `models loaded` / `models file not found` を確認する |
| プルダウンに「APIキー未設定」と出る | `models.yml` の `${VAR}` が参照する環境変数が空。`.env` に設定して `docker compose up -d` |
| コピーボタンが「コピーできません」になる | `http://` かつ localhost 以外で開いている。ブラウザの Clipboard API は https か localhost でしか使えないため、旧方式にフォールバックするが、それも失敗する環境がある |
| ポート 8090 が使用中 | `.env` の `APP_PORT` を変更して `docker compose up -d` |
| 複数 URL で極端に遅い | `LLM_CONCURRENCY` は LLM サーバの同時実行数 (llama.cpp なら `--parallel`) 以下にする。1 の場合は URL ごとに直列処理になる |
| バッジが「本文から自動選択」ばかりになる | LLM が原文をコピーせず創作している。`LLM_TEMPERATURE=0` を確認し、より指示追従性の高いモデルを試す。`FALLBACK_MIN_RATIO` を下げると言い換え元を拾いやすくなる |
| バッジが「近い原文に置換」ばかりになる | LLM が原文をコピーせず言い換えている。出力自体は原文なので実害は無いが、`VERIFY_MIN_RATIO` を少し下げると `原文に補正` に寄る |
| 観点を変えても結果が変わらない | 候補文が少ないと選択肢が無い。`meta.candidate_count` を確認する。`meta.persona_applied` が `false` なら観点が届いていない |
| 設定やお手本が消えた | `docker compose down -v` は named volume ごと削除する。データを残すなら `-v` を付けない |
