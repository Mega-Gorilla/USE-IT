# Sensitive Data（機密情報保護）機能

## 概要

Browser-Useの`sensitive_data`機能は、パスワード、APIキー、ユーザー名などの機密情報をLLMから完全に隠蔽するためのセキュリティ機能です。この機能により、LLMを活用したブラウザ自動化を行いながら、実際の認証情報が一切LLMに公開されることなく安全に運用できます。

### 核心的な仕組み

**2層セキュリティアプローチ:**

1. **入力保護フェーズ**: LLMに送信するメッセージ内で、実際の機密情報を`<secret>キー名</secret>`というプレースホルダータグに置換
2. **実行時置換フェーズ**: ブラウザアクション実行時に、現在のURLがドメインパターンにマッチする場合のみプレースホルダーを実際の値に置換

**重要**: LLMは実際のパスワードやAPIキーを一切見ることができず、常にプレースホルダータグのみを扱います。

---

## アーキテクチャ

### コンポーネント構成

```
┌─────────────────────────────────────────────────────────────┐
│                    Browser-Use Agent                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. 初期化 (Agent.__init__)                                  │
│     ├─ sensitive_data を受け取る                             │
│     ├─ allowed_domains との整合性を検証                     │
│     └─ セキュリティ警告を発行（必要時）                     │
│                                                              │
│  2. メッセージフィルタリング (MessageManager)                │
│     ├─ _filter_sensitive_data()                             │
│     ├─ 実際の値 → <secret>key</secret>                      │
│     └─ フィルタリング済みメッセージをLLMへ送信              │
│                                                              │
│  3. LLM推論                                                  │
│     ├─ プレースホルダータグのみを認識                       │
│     ├─ 実際の値は一切見えない                               │
│     └─ アクションにプレースホルダーを含める                 │
│                                                              │
│  4. アクション実行 (Registry)                                │
│     ├─ _replace_sensitive_data()                            │
│     ├─ 現在URLとドメインパターンを照合                      │
│     ├─ マッチした場合のみ置換                               │
│     └─ ブラウザに実際の値を渡す                             │
│                                                              │
│  5. 履歴保存 (AgentHistory)                                  │
│     ├─ model_dump() で再度フィルタリング                    │
│     └─ 機密情報を含まない安全な履歴を保存                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 実装ファイルマッピング

| コンポーネント | ファイルパス | 行番号 | 責務 |
|--------------|------------|-------|------|
| **メッセージフィルタリング** | `browser_use/agent/message_manager/service.py` | 426-466 | LLMに送信する前に機密情報をプレースホルダーに置換 |
| **値の置換処理** | `browser_use/tools/registry/service.py` | 398-475 | アクション実行時にプレースホルダーを実際の値に置換 |
| **履歴フィルタリング** | `browser_use/agent/views.py` | 298-389 | 保存する履歴から機密情報を除去 |
| **セキュリティ検証** | `browser_use/agent/service.py` | 327-366 | 初期化時にsensitive_dataとallowed_domainsの整合性を検証 |
| **ドメインパターンマッチング** | `browser_use/utils.py` | - | URLとドメインパターンの安全なマッチング処理 |
| **設定定義** | `browser_use/agent/config.py` | 56 | AgentConfigのsensitive_dataフィールド定義 |

---

## 使用方法

### 基本形式1: グローバル認証情報

全てのドメインで有効な認証情報を設定する最もシンプルな形式。

```python
from browser_use import Agent

# グローバルに利用可能な認証情報
sensitive_data = {
	'username': 'my_username',
	'password': 'my_password',
	'api_key': 'secret_key_123'
}

agent = Agent(
	task='Log in to the website and access dashboard',
	llm=llm,
	sensitive_data=sensitive_data
)

await agent.run()
```

**動作例:**
```
LLMが見るメッセージ:
"Please enter <secret>username</secret> and <secret>password</secret>"

LLMの判断:
"I should type <secret>username</secret> in the username field"

ブラウザに送信される実際の値:
"my_username"
```

**注意**: この形式では認証情報が全てのドメインで利用可能になるため、セキュリティリスクが高い。本番環境では推奨されません。

### 基本形式2: ドメイン固有認証情報（推奨）

特定のドメインパターンにのみ認証情報を紐付ける安全な形式。

```python
from browser_use import Agent, BrowserProfile

# ドメインごとに異なる認証情報を設定
sensitive_data = {
	# 完全一致ドメイン
	'https://example.com': {
		'username': 'user@example.com',
		'password': 'example_pass'
	},

	# 別ドメインの認証情報
	'https://api.github.com': {
		'token': 'ghp_xxxxxxxxxxxxxxxxxx'
	},

	# ワイルドカードでサブドメイン全体をカバー
	'https://*.staging.company.com': {
		'api_key': 'staging_secret_key',
		'username': 'staging_user'
	},

	# プロトコルワイルドカード（http/https両対応）
	'http*://internal.company.com': {
		'admin_password': 'admin_secret'
	}
}

# セキュリティ強化: ドメイン制限を設定（必須）
browser_profile = BrowserProfile(
	allowed_domains=[
		'example.com',
		'api.github.com',
		'*.staging.company.com',
		'internal.company.com'
	]
)

agent = Agent(
	task='Access secure resources across multiple services',
	llm=llm,
	sensitive_data=sensitive_data,
	browser_profile=browser_profile  # allowed_domains が必須！
)

await agent.run()
```

**動作の詳細:**

1. **URLマッチング時（例: https://example.com/login）**
   ```python
   # この時点で example.com の認証情報のみが利用可能になる
   # 他のドメインの認証情報は使用不可

   LLMの出力: "Type <secret>username</secret> and <secret>password</secret>"

   # 置換処理:
   # <secret>username</secret> → "user@example.com"
   # <secret>password</secret> → "example_pass"
   # <secret>token</secret> → そのまま（github.comの認証情報は使用不可）
   ```

2. **URL非マッチ時（例: https://unknown-site.com）**
   ```python
   # どの認証情報も利用可能にならない

   LLMの出力: "Type <secret>username</secret>"

   # 置換なし:
   # <secret>username</secret> → "<secret>username</secret>"（そのまま）
   # ブラウザに文字列 "<secret>username</secret>" が入力される
   ```

### 応用例1: 2FA（2要素認証）サポート

TOTP（Time-based One-Time Password）を自動生成して入力する機能。

```python
import pyotp

sensitive_data = {
	'https://secure-site.com': {
		'username': 'user@example.com',
		'password': 'my_password',
		# TOTP シークレットキー（Google Authenticator などで使用）
		'bu_2fa_code_google': 'JBSWY3DPEHPK3PXP'  # Base32エンコードされたシークレット
	}
}

agent = Agent(
	task='Log in to secure-site.com with 2FA',
	llm=llm,
	sensitive_data=sensitive_data,
	browser_profile=BrowserProfile(allowed_domains=['secure-site.com'])
)

await agent.run()
```

**内部処理（`browser_use/tools/registry/service.py:445-448`）:**

```python
# プレースホルダーに "bu_2fa_code" が含まれる場合、TOTP生成
if 'bu_2fa_code' in placeholder:
	# 実行時に現在の6桁TOTPコードを生成
	replacement_value = pyotp.TOTP(value, digits=6).now()  # 例: "123456"
else:
	replacement_value = value
```

**動作フロー:**
```
1. LLMの判断: "Type <secret>bu_2fa_code_google</secret> in 2FA field"
2. 実行時に自動生成: "123456" (現在時刻に基づく6桁コード)
3. ブラウザに入力: "123456"
```

### 応用例2: 複数フォームフィールドの一括入力

```python
# 企業の登録フォーム用の機密情報
company_info = {
	'company_name': 'Acme Corporation',
	'tax_id': '12-3456789',
	'ceo_name': 'John Doe',
	'company_email': 'contact@acme.com',
	'billing_address': '123 Main St, City, State 12345'
}

sensitive_data = {
	'https://vendor-portal.example.com': company_info,
	'https://compliance.example.com': company_info
}

agent = Agent(
	task='Fill out vendor registration form on vendor-portal.example.com',
	llm=llm,
	sensitive_data=sensitive_data,
	browser_profile=BrowserProfile(
		allowed_domains=['*.example.com']
	)
)
```

**LLMが生成する可能性のあるアクション:**
```json
{
  "action": "input_text",
  "params": {
    "index": 5,
    "text": "<secret>company_name</secret>"
  }
}
```

### 応用例3: Azure OpenAI + ゼロデータロギング構成

最高レベルのプライバシーを実現する構成（`examples/features/secure.py:62-74`参照）。

```python
from browser_use import Agent, BrowserProfile
from langchain_openai import ChatAzureOpenAI

# Azure OpenAI Limited Access Program を使用
# → LLMプロバイダー側でデータロギングが完全に無効化される
llm = ChatAzureOpenAI(
	api_version='2024-10-21',
	azure_endpoint='https://your-resource.openai.azure.com',
	deployment_name='gpt-4o',
	temperature=0.0
)

# ドメイン制限 + デフォルト拡張機能無効化
browser_profile = BrowserProfile(
	allowed_domains=['*google.com', 'browser-use.com'],
	enable_default_extensions=False  # uBlock Origin などを無効化
)

sensitive_data = {
	'company_name': 'Top Secret Project',
	'internal_code': 'PROJECT-X-2024'
}

agent = Agent(
	task='Research information about <secret>company_name</secret>',
	llm=llm,
	browser_profile=browser_profile,
	sensitive_data=sensitive_data,
	use_vision=False  # スクリーンショットも無効化（最高レベルのプライバシー）
)

await agent.run(max_steps=10)
```

**セキュリティレベル:**
- ✅ LLMはプレースホルダーのみ認識
- ✅ Azure側でデータロギングなし
- ✅ スクリーンショット無効でビジュアル情報も保護
- ✅ ドメイン制限で意図しないサイトへのアクセスを防止

---

## セキュリティ機能詳細

### 1. メッセージフィルタリング

**実装場所**: `browser_use/agent/message_manager/service.py:427-466`

```python
def _filter_sensitive_data(self, message: BaseMessage) -> BaseMessage:
	"""
	LLMに送信する前にメッセージから機密情報を除去する

	処理内容:
	1. sensitive_data から全ての値を収集（グローバル形式 + ドメイン固有形式）
	2. メッセージ内容をスキャンし、一致する値を全て検出
	3. 一致した値を <secret>キー名</secret> プレースホルダーに置換
	"""
	sensitive_values: dict[str, str] = {}

	# グローバル形式とドメイン固有形式の両方から値を収集
	for key_or_domain, content in self.sensitive_data.items():
		if isinstance(content, dict):
			# ドメイン固有形式: {'https://example.com': {'user': 'admin'}}
			for key, val in content.items():
				if val:  # 空でない値のみ
					sensitive_values[key] = val
		elif content:
			# グローバル形式: {'user': 'admin'}
			sensitive_values[key_or_domain] = content

	# 全ての機密値をプレースホルダーに置換
	for key, val in sensitive_values.items():
		message.content = message.content.replace(val, f'<secret>{key}</secret>')

	return message
```

**置換の例:**

| 元のメッセージ | フィルタリング後 |
|-------------|----------------|
| `"Username: admin, Password: secret123"` | `"Username: <secret>username</secret>, Password: <secret>password</secret>"` |
| `"API Key: sk-abc123def456"` | `"API Key: <secret>api_key</secret>"` |
| `"Token ghp_1234567890abcdefgh"` | `"Token <secret>token</secret>"` |

### 2. ドメインパターンマッチング

**実装場所**: `browser_use/utils.py` の `match_url_with_domain_pattern()`

#### サポートされるパターン

```python
# 1. 完全一致
match_url_with_domain_pattern(
	'https://example.com/login',
	'example.com'
)  # → True

match_url_with_domain_pattern(
	'https://example.com/login',
	'https://example.com'
)  # → True

# 2. ワイルドカードサブドメイン
match_url_with_domain_pattern(
	'https://api.example.com',
	'*.example.com'
)  # → True

match_url_with_domain_pattern(
	'https://sub1.sub2.example.com',
	'*.example.com'
)  # → True (多段階サブドメインも対応)

# 3. プロトコルワイルドカード
match_url_with_domain_pattern(
	'http://example.com',
	'http*://example.com'
)  # → True

match_url_with_domain_pattern(
	'https://example.com',
	'http*://example.com'
)  # → True

# 4. ポート番号は無視される
match_url_with_domain_pattern(
	'https://example.com:8080/api',
	'example.com'
)  # → True

# 5. chrome-extension:// などの特殊スキーム
match_url_with_domain_pattern(
	'chrome-extension://abcdefgh/popup.html',
	'chrome-extension://abcdefgh'
)  # → True
```

#### セキュリティ保護: 危険なパターンの拒否

```python
# ❌ 危険: TLD の前にワイルドカード（フィッシング対策）
match_url_with_domain_pattern(
	'https://example.com',
	'*google.com'
)  # → False (警告ログ出力)
# 理由: example.google.com と example.com を区別できない

# ❌ 危険: 過度に広範なパターン
match_url_with_domain_pattern(
	'https://example.com',
	'*'
)  # → False
# 理由: 全てのドメインにマッチしてしまう

match_url_with_domain_pattern(
	'https://example.com',
	'*.*.com'
)  # → False
# 理由: 複数階層のワイルドカードは予測困難

# ❌ 危険: TLD にワイルドカード
match_url_with_domain_pattern(
	'https://example.com',
	'example.*'
)  # → False
# 理由: example.com, example.net, example.org 全てマッチ

# ❌ 特殊ページは常に無視（認証情報漏洩防止）
match_url_with_domain_pattern(
	'about:blank',
	'example.com'
)  # → False

match_url_with_domain_pattern(
	'chrome://new-tab-page',
	'example.com'
)  # → False
```

**安全性チェックのロジック:**

```python
# browser_use/utils.py の実装より抜粋
unsafe_patterns = [
	'*' in domain_pattern and not domain_pattern.startswith('*.'),
	domain_pattern.count('*') > 1,
	'.*' in domain_pattern,  # TLD ワイルドカード
	'*.' in domain_pattern and domain_pattern.index('*.') > 0,  # 中間ワイルドカード
]

if any(unsafe_patterns):
	if log_warnings:
		logger.warning(f'Unsafe domain pattern detected: {domain_pattern}')
	return False
```

### 3. アクション実行時の置換処理

**実装場所**: `browser_use/tools/registry/service.py:398-475`

```python
def _replace_sensitive_data(
	self,
	params: BaseModel,
	sensitive_data: dict,
	current_url: str | None = None
) -> BaseModel:
	"""
	アクション実行時にプレースホルダーを実際の値に置換

	処理フロー:
	1. 現在のURLに対して有効な認証情報のみを抽出
	2. プレースホルダーを検出
	3. 2FAコードの場合はTOTP生成
	4. それ以外は対応する値で置換
	"""

	# ステップ1: 現在URLに適用可能な認証情報を収集
	applicable_secrets = {}

	for domain_or_key, content in sensitive_data.items():
		if isinstance(content, dict):
			# ドメイン固有形式
			if current_url and match_url_with_domain_pattern(current_url, domain_or_key):
				# URLがパターンにマッチした場合のみ追加
				applicable_secrets.update(content)
		else:
			# グローバル形式（常に追加）
			applicable_secrets[domain_or_key] = content

	# ステップ2: プレースホルダーを置換
	params_dump = params.model_dump_json()

	for placeholder, value in applicable_secrets.items():
		# 2FA コードの特殊処理
		if 'bu_2fa_code' in placeholder:
			# TOTPコードを生成
			replacement_value = pyotp.TOTP(value, digits=6).now()
		else:
			replacement_value = value

		# プレースホルダーを実際の値に置換
		params_dump = params_dump.replace(
			f'<secret>{placeholder}</secret>',
			replacement_value
		)

	# JSON から Pydantic モデルに復元
	processed_params = json.loads(params_dump)
	return type(params).model_validate(processed_params)
```

**置換の実例:**

```python
# 現在URL: https://example.com/login
# sensitive_data:
# {
#   'https://example.com': {'username': 'admin', 'password': 'secret123'},
#   'https://other.com': {'token': 'other_token'}
# }

# LLMのアクション出力:
action_params = {
	"action": "input_text",
	"index": 5,
	"text": "<secret>username</secret>"
}

# _replace_sensitive_data() 実行後:
replaced_params = {
	"action": "input_text",
	"index": 5,
	"text": "admin"  # ← 置換された！
}

# 注意: <secret>token</secret> は置換されない
# 理由: other.com の認証情報は example.com では使用不可
```

### 4. 初期化時のセキュリティ検証

**実装場所**: `browser_use/agent/service.py:327-366`

```python
# Agent.__init__() 内の検証ロジック

if self.sensitive_data:
	# ドメイン固有の認証情報が含まれているかチェック
	has_domain_specific_credentials = any(
		isinstance(v, dict) for v in self.sensitive_data.values()
	)

	# 致命的エラー: allowed_domains が未設定
	if not self.browser_profile.allowed_domains:
		self.logger.error(
			'⚠️ Agent(sensitive_data=••••••••) was provided but '
			'Browser(allowed_domains=[...]) is not locked down! ⚠️\n'
			'☠️ If the agent visits a malicious website and encounters a '
			'prompt-injection attack, your sensitive_data may be exposed!'
		)

	# 警告: ドメインパターンが allowed_domains でカバーされていない
	elif has_domain_specific_credentials:
		domain_patterns = [
			k for k, v in self.sensitive_data.items() if isinstance(v, dict)
		]

		for domain_pattern in domain_patterns:
			is_covered = any(
				match_url_with_domain_pattern(
					f'https://{allowed_domain}',
					domain_pattern
				)
				for allowed_domain in self.browser_profile.allowed_domains
			)

			if not is_covered:
				logger.warning(
					f'⚠️ Domain pattern "{domain_pattern}" in sensitive_data '
					f'is not covered by allowed_domains={self.browser_profile.allowed_domains}. '
					f'This may indicate a configuration error.'
				)
```

**エラー例:**

```python
# ❌ NG: allowed_domains 未設定
agent = Agent(
	sensitive_data={'password': 'secret123'},
	# browser_profile が未設定 or allowed_domains が空
)
# → エラーログ出力:
# ⚠️ sensitive_data が提供されているが allowed_domains が未設定！
# ☠️ プロンプトインジェクション攻撃で認証情報が漏洩する可能性があります！

# ✅ OK: 正しい設定
agent = Agent(
	sensitive_data={
		'https://example.com': {'password': 'secret123'}
	},
	browser_profile=BrowserProfile(
		allowed_domains=['example.com']
	)
)
```

### 5. 履歴保存時のフィルタリング

**実装場所**: `browser_use/agent/views.py:298-389`

```python
class AgentHistory(BaseModel):
	"""エージェントの実行履歴（機密情報フィルタリング付き）"""

	model_config = ConfigDict(arbitrary_types_allowed=True)

	history: list[AgentStepInfo]

	def model_dump(self, **kwargs) -> dict[str, Any]:
		"""
		履歴をシリアライズする際に機密情報を除去

		saved_conversation_path で保存される履歴には
		実際の認証情報が含まれないことを保証
		"""
		result = super().model_dump(**kwargs)

		# 各ステップのアクションから機密情報を除去
		for step in result.get('history', []):
			if 'action' in step:
				action_result = step['action']
				# _filter_sensitive_data() を適用
				# （実装詳細は MessageManager と同様）

		return result
```

**保存される履歴の例:**

```json
{
  "history": [
    {
      "step_number": 1,
      "action": {
        "name": "input_text",
        "params": {
          "index": 5,
          "text": "<secret>username</secret>"
        }
      },
      "result": "Successfully typed text"
    },
    {
      "step_number": 2,
      "action": {
        "name": "input_text",
        "params": {
          "index": 6,
          "text": "<secret>password</secret>"
        }
      },
      "result": "Successfully typed text"
    }
  ]
}
```

**重要**: 実際の`admin`や`secret123`といった値は履歴に残らない。

---

## テストカバレッジ

**テストファイル**: `tests/ci/test_agent_sensitive_data.py` (272行)

### 主要なテストケース

#### 1. 欠落キーの処理
```python
@pytest.mark.asyncio
async def test_replace_sensitive_data_with_missing_keys():
	"""
	一部のキーが欠落している場合の動作確認

	期待される動作:
	- 存在するキー: 置換される
	- 存在しないキー: プレースホルダーのまま残る
	- 空の値: 欠落として扱われる
	"""
	registry = Registry()

	# テスト1: 全キー存在
	sensitive_data = {'username': 'admin', 'password': 'secret'}
	params = InputTextParams(
		index=0,
		text='<secret>username</secret> and <secret>password</secret>'
	)
	result = registry._replace_sensitive_data(params, sensitive_data)
	assert result.text == 'admin and secret'

	# テスト2: 一部キー欠落
	sensitive_data = {'username': 'admin'}  # password なし
	params = InputTextParams(
		index=0,
		text='<secret>username</secret> and <secret>password</secret>'
	)
	result = registry._replace_sensitive_data(params, sensitive_data)
	assert result.text == 'admin and <secret>password</secret>'  # password はそのまま

	# テスト3: 空の値は欠落扱い
	sensitive_data = {'username': '', 'password': 'secret'}
	params = InputTextParams(
		index=0,
		text='<secret>username</secret> and <secret>password</secret>'
	)
	result = registry._replace_sensitive_data(params, sensitive_data)
	assert result.text == '<secret>username</secret> and secret'
```

#### 2. ドメイン固有認証情報
```python
@pytest.mark.asyncio
async def test_simple_domain_specific_sensitive_data():
	"""
	ドメイン固有認証情報のURL依存動作を確認
	"""
	registry = Registry()

	sensitive_data = {
		'https://example.com': {
			'username': 'example_user',
			'password': 'example_pass'
		},
		'https://other.com': {
			'token': 'other_token'
		}
	}

	params = InputTextParams(
		index=0,
		text='<secret>username</secret>, <secret>password</secret>, <secret>token</secret>'
	)

	# テスト1: URL なし → ドメイン固有認証情報は使用不可
	result = registry._replace_sensitive_data(params, sensitive_data, current_url=None)
	assert result.text == '<secret>username</secret>, <secret>password</secret>, <secret>token</secret>'

	# テスト2: example.com の URL → example.com の認証情報のみ使用可
	result = registry._replace_sensitive_data(
		params,
		sensitive_data,
		current_url='https://example.com/login'
	)
	assert result.text == 'example_user, example_pass, <secret>token</secret>'

	# テスト3: other.com の URL → other.com の認証情報のみ使用可
	result = registry._replace_sensitive_data(
		params,
		sensitive_data,
		current_url='https://other.com/api'
	)
	assert result.text == '<secret>username</secret>, <secret>password</secret>, other_token'

	# テスト4: 未登録ドメイン → どの認証情報も使用不可
	result = registry._replace_sensitive_data(
		params,
		sensitive_data,
		current_url='https://unknown.com'
	)
	assert result.text == '<secret>username</secret>, <secret>password</secret>, <secret>token</secret>'
```

#### 3. URLパターンマッチング
```python
def test_match_url_with_domain_pattern():
	"""ドメインパターンマッチングの動作確認"""
	from browser_use.utils import match_url_with_domain_pattern

	# 完全一致
	assert match_url_with_domain_pattern('https://example.com', 'example.com') is True
	assert match_url_with_domain_pattern('https://example.com/path', 'example.com') is True

	# ワイルドカードサブドメイン
	assert match_url_with_domain_pattern('https://api.example.com', '*.example.com') is True
	assert match_url_with_domain_pattern('https://sub.api.example.com', '*.example.com') is True

	# プロトコルワイルドカード
	assert match_url_with_domain_pattern('http://example.com', 'http*://example.com') is True
	assert match_url_with_domain_pattern('https://example.com', 'http*://example.com') is True

	# ポート番号無視
	assert match_url_with_domain_pattern('https://example.com:8080', 'example.com') is True

	# chrome-extension
	assert match_url_with_domain_pattern(
		'chrome-extension://abc123/popup.html',
		'chrome-extension://abc123'
	) is True

	# 特殊ページ（常に False）
	assert match_url_with_domain_pattern('about:blank', 'example.com') is False
	assert match_url_with_domain_pattern('chrome://new-tab-page', 'example.com') is False
```

#### 4. 危険なパターンの拒否
```python
def test_unsafe_domain_patterns():
	"""安全でないドメインパターンが拒否されることを確認"""
	from browser_use.utils import match_url_with_domain_pattern

	# ❌ TLD の前にワイルドカード
	assert match_url_with_domain_pattern('https://example.com', '*google.com') is False

	# ❌ 過度に広範なパターン
	assert match_url_with_domain_pattern('https://example.com', '*') is False
	assert match_url_with_domain_pattern('https://example.com', '*.*.com') is False

	# ❌ TLD にワイルドカード
	assert match_url_with_domain_pattern('https://example.com', 'example.*') is False

	# ❌ 中間にワイルドカード
	assert match_url_with_domain_pattern('https://example.com', '*com*') is False
```

#### 5. メッセージフィルタリング
```python
@pytest.mark.asyncio
async def test_filter_sensitive_data():
	"""MessageManager のフィルタリング機能テスト"""

	# グローバル形式
	sensitive_data = {
		'username': 'admin',
		'password': 'secret123'
	}

	message_manager = MessageManager(sensitive_data=sensitive_data)

	original_message = HumanMessage(
		content='Please log in with username admin and password secret123'
	)

	filtered = message_manager._filter_sensitive_data(original_message)

	assert 'admin' not in filtered.content
	assert 'secret123' not in filtered.content
	assert '<secret>username</secret>' in filtered.content
	assert '<secret>password</secret>' in filtered.content
	assert filtered.content == (
		'Please log in with username <secret>username</secret> '
		'and password <secret>password</secret>'
	)
```

---

## ベストプラクティス

### 1. 必ずドメイン固有形式を使用する

```python
# ❌ NG: グローバル形式（全ドメインで露出）
sensitive_data = {
	'password': 'my_secret_password'
}

# ✅ OK: ドメイン固有形式（特定ドメインのみ）
sensitive_data = {
	'https://trusted-site.com': {
		'password': 'my_secret_password'
	}
}
```

### 2. allowed_domains を必ず設定する

```python
# ❌ NG: allowed_domains 未設定（セキュリティリスク）
agent = Agent(
	sensitive_data={'https://example.com': {'password': 'secret'}},
	# allowed_domains が無い
)
# → プロンプトインジェクション攻撃で意図しないサイトにアクセス可能

# ✅ OK: allowed_domains でアクセス範囲を制限
agent = Agent(
	sensitive_data={'https://example.com': {'password': 'secret'}},
	browser_profile=BrowserProfile(
		allowed_domains=['example.com']
	)
)
```

### 3. 高機密環境では use_vision=False

```python
# ✅ 最高レベルのプライバシー保護
agent = Agent(
	sensitive_data=sensitive_data,
	browser_profile=BrowserProfile(allowed_domains=['trusted.com']),
	use_vision=False  # スクリーンショット無効化
)
```

**理由**: スクリーンショット内に表示された機密情報（パスワードフィールドの内容など）はフィルタリングされずにLLMに送信される可能性があるため。

### 4. Azure OpenAI Limited Access を活用

```python
from langchain_openai import ChatAzureOpenAI

# Azure OpenAI Limited Access Program
# → LLM プロバイダー側でデータロギングを完全無効化
llm = ChatAzureOpenAI(
	api_version='2024-10-21',
	azure_endpoint='https://your-resource.openai.azure.com',
	deployment_name='gpt-4o'
)

agent = Agent(
	llm=llm,
	sensitive_data=sensitive_data,
	use_vision=False
)
```

参考: `examples/features/secure.py:9-39`

### 5. ワイルドカードは最小限に

```python
# ❌ 避けるべき: 過度に広範
sensitive_data = {
	'*.com': {'password': 'secret'}  # 全 .com ドメイン（危険）
}

# ✅ 推奨: 具体的に指定
sensitive_data = {
	'*.mycompany.com': {'password': 'secret'}  # 自社ドメインのみ
}
```

### 6. 2FA は pyotp 互換形式を使用

```python
import pyotp

# ✅ 正しい TOTP シークレット形式
totp_secret = pyotp.random_base32()  # 例: "JBSWY3DPEHPK3PXP"

sensitive_data = {
	'https://secure-site.com': {
		'bu_2fa_code_google': totp_secret  # Base32 エンコード文字列
	}
}
```

**命名規則**: プレースホルダーキーに `bu_2fa_code` を含めることで、自動的にTOTP生成が有効化される。

### 7. テスト時は saved_conversation_path で検証

```python
from pathlib import Path

agent = Agent(
	task='Login to the site',
	llm=llm,
	sensitive_data=sensitive_data,
	browser_profile=BrowserProfile(allowed_domains=['example.com']),
	saved_conversation_path=Path('./conversation_history.json')
)

await agent.run()

# 保存された履歴ファイルを確認
# → 実際の認証情報が含まれていないことを検証
with open('./conversation_history.json') as f:
	history = json.load(f)
	# 'admin' や 'secret123' が含まれていないはず
	# '<secret>username</secret>' のみ含まれるはず
	assert 'admin' not in json.dumps(history)
	assert '<secret>username</secret>' in json.dumps(history)
```

---

## 制限事項と注意点

### 1. スクリーンショットは自動フィルタリングされない

**問題**: デフォルトでは、ブラウザのスクリーンショットがLLMに送信される際、画面上に表示されている機密情報（パスワード入力フィールドの内容など）はフィルタリングされません。

**対策**:
```python
# オプション1: スクリーンショットを完全無効化
agent = Agent(
	use_vision=False,
	sensitive_data=sensitive_data
)

# オプション2: スクリーンショット内の機密情報をマスキング（手動実装が必要）
# → 現時点では標準機能として提供されていない
```

### 2. グローバル形式は本番環境非推奨

**問題**: グローバル形式 `{'password': 'secret'}` は全てのドメインで認証情報が利用可能になる。

**リスク**:
- プロンプトインジェクション攻撃で悪意のあるサイトに誘導された場合
- 誤ってフィッシングサイトにアクセスした場合
- → 認証情報が意図しないドメインで入力される可能性

**推奨**:
```python
# ✅ ドメイン固有形式のみ使用
sensitive_data = {
	'https://legitimate-site.com': {'password': 'secret'}
}
```

### 3. LLM のトレーニングデータ化リスク

**問題**: LLMプロバイダーの設定によっては、送信されたデータ（プレースホルダーを含む）がトレーニングデータとして使用される可能性があります。

**対策**:
```python
# OpenAI API の場合
# → Enterprise プラン or API 契約でゼロデータリテンションを確認

# Azure OpenAI の場合
# → Limited Access Program でデータロギング完全無効化
llm = ChatAzureOpenAI(...)

# その他の LLM プロバイダー
# → 各プロバイダーのプライバシーポリシーを確認
```

### 4. パターンマッチングのパフォーマンス

**問題**: ワイルドカードパターンが多い場合、マッチング処理が遅くなる可能性があります。

**パフォーマンス最適化**:
```python
# ❌ 遅い: 複雑なワイルドカードパターン多数
sensitive_data = {
	'http*://*.sub1.example.com': {...},
	'http*://*.sub2.example.com': {...},
	'http*://*.sub3.example.com': {...},
	# ... 100個以上のパターン
}

# ✅ 速い: 単純化されたパターン
sensitive_data = {
	'*.example.com': {...}  # 1つのパターンで全サブドメインをカバー
}
```

### 5. 履歴ログのサイズ

**問題**: `<secret>very_long_key_name_here</secret>` のような長いプレースホルダーは、ログサイズを増大させます。

**推奨**:
```python
# ✅ 短いキー名を使用
sensitive_data = {
	'https://example.com': {
		'user': 'admin',        # 短い
		'pass': 'secret123',    # 短い
		'key': 'api_key_value'  # 短い
	}
}

# ❌ 避けるべき: 長いキー名
sensitive_data = {
	'https://example.com': {
		'company_admin_username_for_production': 'admin',
		'company_admin_password_for_production': 'secret123'
	}
}
```

---

## トラブルシューティング

### 問題1: プレースホルダーがそのまま入力される

**症状**:
```
ブラウザに "<secret>username</secret>" という文字列が入力される
```

**原因**:
1. 現在のURLがドメインパターンにマッチしていない
2. sensitive_data のキー名とプレースホルダーが一致していない

**解決策**:
```python
# デバッグ: マッチングを確認
from browser_use.utils import match_url_with_domain_pattern

current_url = 'https://example.com/login'
domain_pattern = 'example.com'

result = match_url_with_domain_pattern(current_url, domain_pattern, log_warnings=True)
print(f'Match result: {result}')  # True であるべき

# キー名の一致を確認
sensitive_data = {
	'https://example.com': {
		'username': 'admin',  # ← このキー名
		'password': 'secret'
	}
}

# LLMが使うプレースホルダー: <secret>username</secret>
# ↑ キー名と完全一致する必要がある
```

### 問題2: allowed_domains の警告が出る

**症状**:
```
⚠️ Agent(sensitive_data=••••••••) was provided but
Browser(allowed_domains=[...]) is not locked down!
```

**解決策**:
```python
# allowed_domains を設定
agent = Agent(
	sensitive_data=sensitive_data,
	browser_profile=BrowserProfile(
		allowed_domains=['example.com', 'api.example.com']
	)
)
```

### 問題3: 2FA コードが生成されない

**症状**:
```
プレースホルダー <secret>2fa_code</secret> がそのまま入力される
```

**原因**: プレースホルダーキー名に `bu_2fa_code` が含まれていない

**解決策**:
```python
# ❌ NG: TOTP として認識されない
sensitive_data = {
	'https://example.com': {
		'2fa_code': 'JBSWY3DPEHPK3PXP'
	}
}

# ✅ OK: TOTP として認識される
sensitive_data = {
	'https://example.com': {
		'bu_2fa_code_google': 'JBSWY3DPEHPK3PXP'  # bu_2fa_code を含む
	}
}
```

### 問題4: ワイルドカードが動作しない

**症状**:
```
*.example.com が api.example.com にマッチしない
```

**原因**: プロトコルの不一致

**解決策**:
```python
# ❌ NG: プロトコルが違うためマッチしない
domain_pattern = 'http://*.example.com'
current_url = 'https://api.example.com'  # https

# ✅ OK: プロトコルワイルドカードを使用
domain_pattern = 'http*://*.example.com'
current_url = 'https://api.example.com'  # マッチする

# ✅ OK: プロトコルなしでもマッチする
domain_pattern = '*.example.com'
current_url = 'https://api.example.com'  # マッチする
```

---

## まとめ

Browser-Use の sensitive_data 機能は、以下の特徴を持つ高度なセキュリティ機能です：

### ✅ 主要機能
1. **2層保護**: LLMへの入力時フィルタリング + 実行時置換
2. **ドメインスコープ**: 認証情報を特定ドメインに限定
3. **パターンマッチング**: ワイルドカードサポート + 危険パターン拒否
4. **2FA サポート**: TOTP 自動生成
5. **履歴フィルタリング**: 保存される履歴から機密情報を除去

### ✅ セキュリティ保証
- LLMは実際の認証情報を一切見ない（プレースホルダーのみ）
- ドメイン外では認証情報が使用されない
- allowed_domains と組み合わせて多層防御

### ⚠️ 注意点
- スクリーンショットは別途対策が必要（use_vision=False 推奨）
- グローバル形式は本番環境非推奨
- LLMプロバイダーのプライバシーポリシー確認必須

### 📚 参考リソース
- 実装: `browser_use/agent/message_manager/service.py:426-466`
- テスト: `tests/ci/test_agent_sensitive_data.py`
- 使用例: `examples/features/sensitive_data.py`, `examples/features/secure.py`

---

## 関連ドキュメント

- [Agent実行フロー](../agent/agent_flow.md) - Agentの全体的な実行フローとsensitive_dataの位置付け
- [Browser Profile設定](../../browser_use/browser/profile.py) - allowed_domainsの詳細設定
- [Tools/Registry](../../browser_use/tools/registry/service.py) - アクション実行時の置換処理の実装

---

**最終更新**: 2025-10-17
