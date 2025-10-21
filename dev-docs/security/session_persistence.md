# Session Persistence（セッション永続化）機能

## 概要

Browser-Useは、ブラウザに保存された認証情報（Cookie、localStorage、sessionStorage）を永続化・再利用する機能を提供します。これにより、毎回ログインし直すことなく、以前のセッションを継続してブラウザ自動化を実行できます。

### 2つの永続化メカニズム

Browser-Useは相補的な2つのセッション永続化方式を提供しています：

1. **ネイティブブラウザプロファイル方式** (`user_data_dir` + `profile_directory`)
   - Chromeの標準プロファイル機能を使用
   - すべてのブラウザデータをネイティブ形式で保存
   - 既存のChromeプロファイルをそのまま利用可能

2. **Storage State エクスポート/インポート方式** (`storage_state`)
   - JSON形式でCookieを保存（localStorage/sessionStorageは自動保存経路でのみ対応）
   - 30秒ごとの定期自動保存（ポーリング方式）
   - Playwright互換フォーマット
   - 一時的なセッションやクロスブラウザ互換性に最適

---

## アーキテクチャ

### コンポーネント構成

```
┌─────────────────────────────────────────────────────────────┐
│                    Browser-Use Session                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. ブラウザプロファイル管理 (BrowserProfile)                │
│     ├─ user_data_dir: プロファイルディレクトリ               │
│     ├─ profile_directory: プロファイル名                     │
│     └─ storage_state: Cookie/storageのJSON                  │
│                                                              │
│  2. ブラウザ起動 (LocalBrowserWatchdog)                      │
│     ├─ Chromeを--user-data-dirで起動                        │
│     ├─ プロファイルロックエラーを自動回復                   │
│     └─ 一時ディレクトリへのフォールバック                   │
│                                                              │
│  3. Storage State管理 (StorageStateWatchdog)                 │
│     ├─ 起動時: storage_state.jsonから自動ロード             │
│     ├─ 実行中: 30秒ごとに自動保存（ポーリング）            │
│     └─ 終了時: 最終状態を永続化                            │
│                                                              │
│  4. CDP統合 (BrowserSession)                                 │
│     ├─ Storage.getCookies: Cookie取得                       │
│     ├─ Storage.setCookies: Cookie設定                       │
│     ├─ DOMStorage: localStorage/sessionStorage操作          │
│     └─ Page.addScriptToEvaluateOnNewDocument: 復元スクリプト│
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 実装ファイルマッピング

| コンポーネント | ファイルパス | 行番号 | 責務 |
|--------------|------------|-------|------|
| **BrowserProfile** | `browser_use/browser/profile.py` | 486-506, 638 | プロファイル設定とディレクトリ管理 |
| **StorageStateWatchdog** | `browser_use/browser/watchdogs/storage_state_watchdog.py` | - | 自動保存・ロード機能 |
| **Session Storage管理** | `browser_use/browser/session.py` | 990-1033, 2136-2319 | Cookie/storageの取得・設定 |
| **LocalBrowserWatchdog** | `browser_use/browser/watchdogs/local_browser_watchdog.py` | 46-206 | ブラウザ起動とプロファイル適用 |
| **Config管理** | `browser_use/config.py` | 160-213, 472-485 | デフォルトパスと権限設定 |
| **イベント定義** | `browser_use/browser/events.py` | 474-511 | 保存/ロードイベント |

---

## 使用方法

### 方式1: ネイティブブラウザプロファイル

既存のChromeプロファイルを使用する最も直接的な方法。

#### 基本的な使用例

```python
from browser_use import Agent, Browser

# 既存のChromeプロファイルを使用
browser = Browser(
	executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
	user_data_dir='~/Library/Application Support/Google/Chrome',
	profile_directory='Default'  # または 'Profile 1', 'Profile 2' など
)

agent = Agent(
	task='Visit Gmail and check emails',
	llm=llm,
	browser=browser
)

await agent.run()
```

**動作:**
- Chromeプロファイルに保存された全ての認証情報（Cookie、パスワード、拡張機能など）がそのまま利用可能
- Gmail、Twitter、GitHubなど、既にログイン済みのサービスに再ログイン不要でアクセス

#### プラットフォーム別デフォルトパス

**macOS:**
```python
browser = Browser(
	executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
	user_data_dir='~/Library/Application Support/Google/Chrome',
	profile_directory='Default'
)
```

**Windows:**
```python
browser = Browser(
	executable_path='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
	user_data_dir='%LOCALAPPDATA%\\Google\\Chrome\\User Data',
	profile_directory='Default'
)
```

**Linux:**
```python
browser = Browser(
	executable_path='/usr/bin/google-chrome',
	user_data_dir='~/.config/google-chrome',
	profile_directory='Default'
)
```

#### カスタムプロファイルの作成

```python
from pathlib import Path

# Browser-Use専用のプロファイルを作成
custom_profile = Path.home() / '.config/browseruse/profiles/my-project'

browser = Browser(
	user_data_dir=custom_profile,
	profile_directory='Default'
)

agent = Agent(
	task='Log in to https://example.com with username admin',
	llm=llm,
	browser=browser
)

await agent.run()

# 次回実行時、example.comへのログイン状態が保持される
```

### 方式2: Storage State（JSON形式）

Cookie、localStorage、sessionStorageをJSON形式でエクスポート/インポートする方式。

#### 基本的な使用例

```python
from browser_use import Agent, Browser
from pathlib import Path

# 初回実行: ログイン後にStorage Stateを保存
browser = Browser()

agent = Agent(
	task='Log in to https://example.com and save credentials',
	llm=llm,
	browser=browser
)

await agent.run()

# Cookieを保存（localStorage/sessionStorageは自動保存のみ対応）
await browser.export_storage_state('example_session.json')
```

```python
# 2回目以降: 保存したStorage Stateを読み込んで再利用
browser = Browser(
	storage_state='example_session.json'
)

agent = Agent(
	task='Access https://example.com dashboard (already logged in)',
	llm=llm,
	browser=browser
)

await agent.run()
# ログイン済み状態でダッシュボードにアクセスできる
```

#### 自動保存機能

Storage Stateは自動的に保存されます：

```python
from browser_use import Browser

browser = Browser(
	storage_state='auto_saved_session.json'  # ファイルパス指定
)

# 以下のタイミングで自動保存:
# 1. ブラウザ起動時: ファイルから自動ロード
# 2. 実行中: 30秒ごとに自動保存（ポーリング方式）
# 3. ブラウザ終了時: 最終状態を保存

agent = Agent(
	task='Multiple operations requiring login',
	llm=llm,
	browser=browser
)

await agent.run()
# 自動的に auto_saved_session.json に保存される
```

#### Storage State の手動エクスポート

```python
from browser_use import Browser

browser = Browser(
	user_data_dir='~/Library/Application Support/Google/Chrome',
	profile_directory='Default'
)

async def export_cookies():
	await browser.start()

	# 現在のCookieをJSONに保存
	# 注意: export_storage_state() はCookieのみを保存します
	# localStorage/sessionStorage は自動保存メカニズム経由でのみ保存されます
	storage_state = await browser.export_storage_state('my_cookies.json')

	print(f"Exported {len(storage_state['cookies'])} cookies")
	# origins は常に空リスト（localStorage/sessionStorageは含まれない）

await export_cookies()
```

#### dict形式での使用

```python
# メモリ上でStorage Stateを管理
storage_state_dict = {
	'cookies': [
		{
			'name': 'session_id',
			'value': 'abc123...',
			'domain': '.example.com',
			'path': '/',
			'expires': 1735689600,
			'httpOnly': True,
			'secure': True,
			'sameSite': 'Lax'
		}
	],
	'origins': []
}

browser = Browser(
	storage_state=storage_state_dict  # dictオブジェクトを直接渡す
)

# 注意: dict形式の場合、自動保存は行われない（ファイルパスが無いため）
```

### 応用例1: 複数サービスへの自動ログイン

```python
from browser_use import Agent, Browser
from pathlib import Path

# サービスごとにStorage Stateを保存
services = {
	'gmail': 'storage_states/gmail.json',
	'github': 'storage_states/github.json',
	'aws': 'storage_states/aws.json'
}

async def login_to_service(service_name: str, login_task: str):
	"""初回ログインしてStorage Stateを保存"""
	browser = Browser()

	agent = Agent(
		task=login_task,
		llm=llm,
		browser=browser
	)

	await agent.run()
	await browser.export_storage_state(services[service_name])
	print(f"{service_name} credentials saved!")

async def use_service(service_name: str, task: str):
	"""保存されたStorage Stateを使用"""
	browser = Browser(
		storage_state=services[service_name]
	)

	agent = Agent(
		task=task,
		llm=llm,
		browser=browser
	)

	await agent.run()

# 初回: ログイン
await login_to_service('gmail', 'Log in to Gmail with my credentials')
await login_to_service('github', 'Log in to GitHub')

# 2回目以降: ログイン不要で利用
await use_service('gmail', 'Check unread emails and summarize')
await use_service('github', 'Create a new repository')
```

### 応用例2: セッション共有（CI/CD環境）

```python
# ローカルで認証情報を取得
from browser_use import Browser

browser = Browser()
# 手動ログイン...
await browser.export_storage_state('ci_session.json')

# ci_session.json をリポジトリにコミット（暗号化推奨）
# または環境変数/シークレット管理ツールに保存
```

```yaml
# GitHub Actions での使用例
name: Browser Automation

on: [push]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Run browser automation
        env:
          STORAGE_STATE: ${{ secrets.STORAGE_STATE_JSON }}
        run: |
          echo "$STORAGE_STATE" > session.json
          python automation_script.py
```

```python
# automation_script.py (CI環境)
from browser_use import Browser

browser = Browser(
	storage_state='session.json'  # GitHub Secretsから復元
)

# ログイン不要で自動化実行
```

---

## Storage State フォーマット

### JSON構造

Storage StateはPlaywright互換のJSON形式を使用します。

```json
{
  "cookies": [
    {
      "name": "session_id",
      "value": "abc123def456...",
      "domain": ".example.com",
      "path": "/",
      "expires": 1735689600,
      "httpOnly": true,
      "secure": true,
      "sameSite": "Lax"
    },
    {
      "name": "user_pref",
      "value": "dark_mode",
      "domain": "example.com",
      "path": "/",
      "expires": -1,
      "httpOnly": false,
      "secure": false,
      "sameSite": "Lax"
    }
  ],
  "origins": [
    {
      "origin": "https://example.com",
      "localStorage": [
        {
          "name": "theme",
          "value": "dark"
        },
        {
          "name": "last_visit",
          "value": "2025-10-21T12:00:00Z"
        }
      ],
      "sessionStorage": [
        {
          "name": "temp_data",
          "value": "some_value"
        }
      ]
    }
  ]
}
```

### Cookie属性の詳細

| 属性 | 型 | 説明 | 例 |
|-----|---|------|---|
| `name` | string | Cookie名 | `"session_id"` |
| `value` | string | Cookie値 | `"abc123..."` |
| `domain` | string | 有効ドメイン | `".example.com"` (サブドメイン含む) |
| `path` | string | 有効パス | `"/"` (全パス) |
| `expires` | number | 有効期限（Unix時刻） | `1735689600` (-1は無期限) |
| `httpOnly` | boolean | HTTPのみアクセス可 | `true` (JavaScriptからアクセス不可) |
| `secure` | boolean | HTTPS必須 | `true` |
| `sameSite` | string | Same-Site設定 | `"Lax"`, `"Strict"`, `"None"` |

### localStorage / sessionStorage 形式

```json
{
  "origin": "https://example.com",
  "localStorage": [
    {"name": "key1", "value": "value1"},
    {"name": "key2", "value": "value2"}
  ],
  "sessionStorage": [
    {"name": "temp_key", "value": "temp_value"}
  ]
}
```

**重要な違い:**
- **localStorage**: ブラウザを閉じても永続化される
- **sessionStorage**: ブラウザを閉じると消去される（Storage Stateでは永続化可能）

---

## 自動保存メカニズム

### StorageStateWatchdog

**実装場所**: `browser_use/browser/watchdogs/storage_state_watchdog.py`

#### 設定オプション

```python
from browser_use import Browser

browser = Browser(
	storage_state='auto_saved.json',
	# StorageStateWatchdog の設定（内部的に適用）
	# auto_save_interval=30.0,  # 30秒ごとに自動保存（ポーリング方式）
)
```

#### 自動保存のタイミング

```
1. ブラウザ起動時
   └─ storage_state.json を読み込み
   └─ Cookieを CDP Storage.setCookies で適用
   └─ localStorage/sessionStorage を初期化スクリプトで復元

2. 実行中（定期保存）
   └─ 30秒ごとに現在のCookieを取得（ポーリング方式）
   └─ 前回保存時から変更があれば storage_state.json に保存

3. ブラウザ終了時
   └─ 最終的なCookieとstorageを取得
   └─ storage_state.json に保存
```

### 保存処理の詳細

**実装場所**: `browser_use/browser/watchdogs/storage_state_watchdog.py:164-228`

```python
async def _save_storage_state(self, path: str | Path) -> dict:
	"""
	Storage Stateを安全に保存

	処理フロー:
	1. 既存ファイルのバックアップ作成
	2. 現在のStorage Stateを取得（CDP経由）
	3. 既存状態とマージ（新しい値が優先）
	4. アトミックな書き込み（一時ファイル→rename）
	"""

	# ステップ1: 既存ファイルのバックアップ
	if path.exists():
		backup_path = path.with_suffix('.json.bak')
		shutil.copy2(path, backup_path)

	# ステップ2: 現在のStorage Stateを取得
	current_state = await self.browser_session._cdp_get_storage_state()

	# ステップ3: 既存状態とマージ
	if path.exists():
		with open(path, 'r') as f:
			existing_state = json.load(f)
		merged_state = self._merge_storage_states(existing_state, current_state)
	else:
		merged_state = current_state

	# ステップ4: アトミック書き込み
	temp_path = path.with_suffix('.json.tmp')
	with open(temp_path, 'w') as f:
		json.dump(merged_state, f, indent=2)

	temp_path.rename(path)  # アトミック操作

	return merged_state
```

### マージ戦略

**実装場所**: `browser_use/browser/watchdogs/storage_state_watchdog.py:286-307`

```python
@staticmethod
def _merge_storage_states(existing: dict, new: dict) -> dict:
	"""
	既存と新規のStorage Stateをマージ

	戦略:
	- Cookie: (name, domain, path) をキーとして、新しい値で上書き
	- Origins: origin URLをキーとして、新しい値で完全置換
	"""

	# Cookieのマージ
	cookie_key = lambda c: (c['name'], c['domain'], c['path'])
	existing_cookies = {cookie_key(c): c for c in existing.get('cookies', [])}

	for new_cookie in new.get('cookies', []):
		existing_cookies[cookie_key(new_cookie)] = new_cookie

	# Originsのマージ
	origin_key = lambda o: o['origin']
	existing_origins = {origin_key(o): o for o in existing.get('origins', [])}

	for new_origin in new.get('origins', []):
		existing_origins[origin_key(new_origin)] = new_origin

	return {
		'cookies': list(existing_cookies.values()),
		'origins': list(existing_origins.values())
	}
```

---

## CDP統合

### Cookie取得

**実装場所**: `browser_use/browser/session.py:2136-2142`

```python
async def _cdp_get_cookies(self) -> list[Cookie]:
	"""CDP Storage.getCookies でCookieを取得"""
	cdp_session = await self.get_or_create_cdp_session(target_id=None, new_socket=False)

	result = await asyncio.wait_for(
		cdp_session.cdp_client.send.Storage.getCookies(session_id=cdp_session.session_id),
		timeout=8.0
	)

	return result.get('cookies', [])
```

**重要**: CDP経由でCookieを取得することで、macOSのKeychain暗号化をバイパスし、復号化されたCookie値を直接取得できます。

### Cookie設定

**実装場所**: `browser_use/browser/session.py:2144-2154`

```python
async def _cdp_set_cookies(self, cookies: list[Cookie]) -> None:
	"""CDP Storage.setCookies でCookieを設定"""
	if not self.agent_focus or not cookies:
		return

	cdp_session = await self.get_or_create_cdp_session(target_id=None, new_socket=False)

	await cdp_session.cdp_client.send.Storage.setCookies(
		params={'cookies': cookies},
		session_id=cdp_session.session_id,
	)
```

### localStorage/sessionStorage 取得

**実装場所**: `browser_use/browser/session.py:2233-2306`

```python
async def _cdp_get_origins(self) -> list[dict[str, Any]]:
	"""
	CDP DOMStorageでlocalStorage/sessionStorageを取得

	処理フロー:
	1. Page.getFrameTree でフレーム階層を取得
	2. 全フレームからユニークなoriginを抽出
	3. 各originのlocalStorage/sessionStorageをDOMStorage.getDOMStorageItemsで取得
	"""

	# ステップ1: フレームツリー取得
	frame_tree = await cdp_client.send.Page.getFrameTree(session_id=session_id)

	# ステップ2: originを再帰的に抽出
	def extract_origins(frame: dict, origins: set):
		url = frame.get('frame', {}).get('url', '')
		if url and not url.startswith('about:'):
			parsed = urlparse(url)
			origin = f"{parsed.scheme}://{parsed.netloc}"
			origins.add(origin)

		for child_frame in frame.get('childFrames', []):
			extract_origins(child_frame, origins)

	origins = set()
	extract_origins(frame_tree['frameTree'], origins)

	# ステップ3: 各originのstorageを取得
	results = []
	for origin in origins:
		# localStorage取得
		local_storage_id = {'securityOrigin': origin, 'isLocalStorage': True}
		local_items = await cdp_client.send.DOMStorage.getDOMStorageItems(
			params={'storageId': local_storage_id},
			session_id=session_id
		)

		# sessionStorage取得
		session_storage_id = {'securityOrigin': origin, 'isLocalStorage': False}
		session_items = await cdp_client.send.DOMStorage.getDOMStorageItems(
			params={'storageId': session_storage_id},
			session_id=session_id
		)

		results.append({
			'origin': origin,
			'localStorage': [{'name': k, 'value': v} for k, v in local_items.get('entries', [])],
			'sessionStorage': [{'name': k, 'value': v} for k, v in session_items.get('entries', [])]
		})

	return results
```

### Storage復元スクリプト

ブラウザ起動時に、localStorage/sessionStorageを復元するJavaScriptを注入します。

**実装場所**: `browser_use/browser/watchdogs/storage_state_watchdog.py`（load処理内）

```python
async def _load_storage_state(self, path: str | Path) -> dict:
	"""Storage Stateを読み込んでブラウザに適用"""

	# Cookieを設定
	await self.browser_session._cdp_set_cookies(storage_state['cookies'])

	# localStorage/sessionStorage を復元するスクリプトを注入
	for origin_data in storage_state.get('origins', []):
		script = f"""
		(function() {{
			const origin = '{origin_data['origin']}';

			// localStorage復元
			{self._generate_storage_restore_script(origin_data['localStorage'], 'localStorage')}

			// sessionStorage復元
			{self._generate_storage_restore_script(origin_data['sessionStorage'], 'sessionStorage')}
		}})();
		"""

		await cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
			params={'source': script},
			session_id=session_id
		)
```

---

## 設定とデフォルトパス

### config.yaml での設定

**実装場所**: `browser_use/config.py:160-213`

```yaml
paths:
  config_home: ~/.config
  config_dir: ~/.config/browseruse
  profiles_dir: ~/.config/browseruse/profiles
  default_user_data_dir: ~/.config/browseruse/profiles/default
  downloads_dir: ~/.config/browseruse/downloads
  extensions_dir: ~/.config/browseruse/extensions

browser:
  headless: false
  user_data_dir: null  # nullの場合、default_user_data_dirを使用
  profile_directory: Default
  storage_state: null  # 指定するとStorage State機能が有効化
```

### プロパティアクセス

```python
from browser_use.config import CONFIG

# デフォルトプロファイルディレクトリ
profiles_dir = CONFIG.BROWSER_USE_PROFILES_DIR
# → Path('~/.config/browseruse/profiles')

# デフォルトuser_data_dir
user_data_dir = CONFIG.BROWSER_USE_DEFAULT_USER_DATA_DIR
# → Path('~/.config/browseruse/profiles/default')
```

### セキュアなディレクトリ作成

**実装場所**: `browser_use/config.py:186-201`

```python
def _ensure_directory(directory: Path) -> None:
	"""
	ディレクトリを安全な権限で作成

	POSIX系OS（Linux/macOS）では chmod 700 を適用
	→ オーナーのみアクセス可能（他ユーザーから保護）
	"""
	try:
		directory.mkdir(parents=True, exist_ok=True)

		if os.name != 'nt':  # Windows以外
			try:
				directory.chmod(0o700)  # rwx------
			except PermissionError:
				logger.warning(
					f'Could not set permissions on {directory}; '
					f'please ensure it is not world-readable.'
				)
	except Exception as exc:
		logger.warning(f'Failed to create directory {directory}: {exc}')
```

**権限の意味:**
- `0o700` = `rwx------`
  - Owner: Read, Write, Execute
  - Group: なし
  - Others: なし

→ 機密情報（Cookie、認証情報）を含むプロファイルディレクトリを他ユーザーから保護

---

## イベントシステム

### Storage State関連イベント

**実装場所**: `browser_use/browser/events.py:474-511`

```python
class SaveStorageStateEvent(BaseEvent):
	"""Storage State保存リクエスト"""
	path: str | None = None  # Noneの場合、プロファイルデフォルトを使用
	event_timeout: float | None = 45.0

class StorageStateSavedEvent(BaseEvent):
	"""Storage State保存完了通知"""
	path: str
	cookies_count: int
	origins_count: int

class LoadStorageStateEvent(BaseEvent):
	"""Storage Stateロードリクエスト"""
	path: str | None = None
	event_timeout: float | None = 45.0

class StorageStateLoadedEvent(BaseEvent):
	"""Storage Stateロード完了通知"""
	path: str
	cookies_count: int
	origins_count: int
```

### イベント駆動フロー

```python
from browser_use import Browser
from browser_use.browser.events import SaveStorageStateEvent, StorageStateSavedEvent

browser = Browser()

# イベントバスでStorage State保存をリクエスト
await browser.event_bus.emit(SaveStorageStateEvent(path='custom_save.json'))

# 保存完了イベントを待機
def on_saved(event: StorageStateSavedEvent):
	print(f"Saved {event.cookies_count} cookies to {event.path}")

browser.event_bus.on(StorageStateSavedEvent, on_saved)
```

---

## ブラウザ起動とプロファイル適用

### LocalBrowserWatchdog

**実装場所**: `browser_use/browser/watchdogs/local_browser_watchdog.py:46-206`

#### 起動コマンド生成

```python
async def _launch_browser(self, max_retries: int = 3) -> tuple[psutil.Process, str]:
	"""
	user_data_dir と profile_directory を指定してChromeを起動

	生成されるChromeコマンド例:
	/Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
		--user-data-dir=/Users/user/.config/browseruse/profiles/default \
		--profile-directory=Default \
		--remote-debugging-port=9222 \
		--no-first-run \
		--no-default-browser-check \
		...
	"""

	launch_args = self.browser_profile.get_args()
	# launch_args には以下が含まれる:
	# - --user-data-dir={user_data_dir}
	# - --profile-directory={profile_directory}
	# - その他のChrome起動オプション

	process = subprocess.Popen([executable_path] + launch_args)
	return process, debug_port
```

#### プロファイルロックエラーの自動回復

```python
async def _launch_browser(self, max_retries: int = 3):
	"""
	Chromeプロファイルロックエラーを自動回復

	エラーシナリオ:
	1. Chromeが既に起動中で user_data_dir がロックされている
	2. 前回のChromeが正常終了せず、ロックファイルが残っている

	回復処理:
	1. 一時ディレクトリを作成
	2. 一時ディレクトリでChromeを起動（リトライ）
	3. 成功したら元の user_data_dir に戻す
	"""

	original_user_data_dir = self.browser_profile.user_data_dir

	for attempt in range(max_retries):
		try:
			process, port = await self._start_chrome_process()
			return process, port

		except Exception as e:
			if 'lock' in str(e).lower() and attempt < max_retries - 1:
				# プロファイルロックエラー検出
				logger.warning(f"Profile locked, creating temp directory (attempt {attempt + 1})")

				# 一時ディレクトリ作成
				temp_dir = tempfile.mkdtemp(prefix='browser-use-temp-')
				self.browser_profile.user_data_dir = temp_dir

				continue
			else:
				raise

	# リトライ成功後、元のディレクトリに戻す
	self.browser_profile.user_data_dir = original_user_data_dir
```

---

## ベストプラクティス

### 1. 用途に応じた方式の選択

**ネイティブプロファイル（user_data_dir）を使うべき場合:**
- ✅ 長期間同じプロファイルを使い続ける
- ✅ 既存のChromeプロファイルをそのまま利用したい
- ✅ 拡張機能やブラウザ設定も含めて永続化したい
- ✅ ローカル開発環境

```python
browser = Browser(
	user_data_dir='~/.config/browseruse/profiles/my-project',
	profile_directory='Default'
)
```

**Storage State（JSON）を使うべき場合:**
- ✅ 一時的なセッション（テスト、CI/CD）
- ✅ クロスプラットフォーム対応が必要
- ✅ バージョン管理システムで管理したい（暗号化推奨）
- ✅ Playwright互換性が必要

```python
browser = Browser(
	storage_state='session.json'
)
```

### 2. セキュリティ考慮事項

```python
from pathlib import Path

# ❌ NG: ホームディレクトリ直下に平文で保存
browser = Browser(
	storage_state='~/session.json'  # 他ユーザーから読み取り可能
)

# ✅ OK: 安全なディレクトリ（chmod 700）に保存
secure_dir = Path.home() / '.config/browseruse/sessions'
secure_dir.mkdir(parents=True, exist_ok=True)
secure_dir.chmod(0o700)

browser = Browser(
	storage_state=secure_dir / 'session.json'
)
```

### 3. CI/CD環境での使用

```python
import os
import json
from pathlib import Path

# GitHub Secrets や AWS Secrets Manager から取得
storage_state_json = os.environ.get('STORAGE_STATE_JSON')

if storage_state_json:
	# JSON文字列 → dict → Browser
	storage_state = json.loads(storage_state_json)
	browser = Browser(storage_state=storage_state)
else:
	# フォールバック: ファイルから読み込み
	browser = Browser(storage_state='ci_session.json')
```

### 4. マルチアカウント管理

```python
from browser_use import Browser
from pathlib import Path

# アカウントごとにプロファイルを分離
accounts = {
	'admin': Path.home() / '.config/browseruse/profiles/admin',
	'user1': Path.home() / '.config/browseruse/profiles/user1',
	'user2': Path.home() / '.config/browseruse/profiles/user2',
}

async def run_as_account(account_name: str, task: str):
	browser = Browser(
		user_data_dir=accounts[account_name],
		profile_directory='Default'
	)

	agent = Agent(
		task=task,
		llm=llm,
		browser=browser
	)

	await agent.run()

# 異なるアカウントで並列実行可能
await run_as_account('admin', 'Perform admin task')
await run_as_account('user1', 'Perform user task')
```

### 5. Storage State のバージョン管理

```python
from datetime import datetime
from pathlib import Path

# タイムスタンプ付きバックアップ
def backup_storage_state(original_path: Path):
	timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
	backup_path = original_path.with_suffix(f'.{timestamp}.json')

	if original_path.exists():
		shutil.copy2(original_path, backup_path)

	return backup_path

# 使用例
session_path = Path('session.json')
backup_backup_storage_state(session_path)

browser = Browser(storage_state=session_path)
# 新しい変更が session.json に保存される
# 古いバージョンは session.20251021_120000.json として保存済み
```

### 6. プロファイルのクリーンアップ

```python
import shutil
from pathlib import Path

def cleanup_old_profiles(days_old: int = 30):
	"""
	古いプロファイルを削除

	注意: 実行前に重要なデータのバックアップを確認
	"""
	from datetime import datetime, timedelta

	profiles_dir = Path.home() / '.config/browseruse/profiles'
	cutoff_time = datetime.now() - timedelta(days=days_old)

	for profile_path in profiles_dir.iterdir():
		if not profile_path.is_dir():
			continue

		# 最終更新時刻をチェック
		mtime = datetime.fromtimestamp(profile_path.stat().st_mtime)

		if mtime < cutoff_time:
			print(f"Removing old profile: {profile_path}")
			shutil.rmtree(profile_path)

# 定期的にクリーンアップ
cleanup_old_profiles(days_old=30)
```

---

## トラブルシューティング

### 問題1: プロファイルロックエラー

**症状:**
```
Error: Cannot acquire lock on user data directory
```

**原因:**
- Chromeが既に同じuser_data_dirで起動中
- 前回のChromeが異常終了し、ロックファイルが残っている

**解決策:**

```python
# オプション1: 既存のChromeプロセスを終了
import psutil

for proc in psutil.process_iter(['name']):
	if 'chrome' in proc.info['name'].lower():
		proc.kill()

# オプション2: 別のuser_data_dirを使用
browser = Browser(
	user_data_dir='~/.config/browseruse/profiles/alternative',
	profile_directory='Default'
)

# オプション3: ロックファイルを手動削除（非推奨）
import os
lock_file = Path('~/.config/browseruse/profiles/default/SingletonLock').expanduser()
if lock_file.exists():
	os.remove(lock_file)
```

### 問題2: Cookieが保存されない

**症状:**
```
次回実行時にログアウト状態になっている
```

**原因:**
- `storage_state` がdict形式で渡されている（ファイルパスが無い）
- `auto_save_interval` が無効化されている
- ブラウザが異常終了している

**解決策:**

```python
# ❌ NG: dict形式（自動保存されない）
browser = Browser(
	storage_state={'cookies': [], 'origins': []}
)

# ✅ OK: ファイルパス形式（自動保存される）
browser = Browser(
	storage_state='session.json'
)

# 確実に保存するには、終了前に明示的にエクスポート
await browser.export_storage_state('session.json')
await browser.stop()
```

### 問題3: localStorage が復元されない

**症状:**
```
Cookieは復元されるが、localStorageの値が消えている
```

**原因:**
- CDP DOMStorageの有効化に失敗
- originのURLが変更されている
- ページ読み込み前にlocalStorageにアクセスしている

**解決策:**

```python
# Storage Stateのoriginsを確認
import json

with open('session.json') as f:
	state = json.load(f)
	print("Origins:", [o['origin'] for o in state.get('origins', [])])

# 正しいoriginが含まれているか確認
# 例: "https://example.com" が必要なのに "http://example.com" しかない

# 解決: 正しいプロトコルでアクセス
agent = Agent(
	task='Visit https://example.com',  # httpsを指定
	llm=llm,
	browser=browser
)
```

### 問題4: 権限エラー

**症状:**
```
PermissionError: [Errno 13] Permission denied: '~/.config/browseruse/profiles/default'
```

**原因:**
- ディレクトリのパーミッションが間違っている
- 別ユーザーが作成したディレクトリを使おうとしている

**解決策:**

```bash
# ディレクトリの所有者とパーミッションを確認
ls -la ~/.config/browseruse/profiles/

# 所有者を変更
sudo chown -R $USER:$USER ~/.config/browseruse/

# パーミッションを修正
chmod 700 ~/.config/browseruse/profiles/default
```

```python
# Pythonコードで権限を設定
from pathlib import Path

profile_dir = Path.home() / '.config/browseruse/profiles/default'
profile_dir.mkdir(parents=True, exist_ok=True)
profile_dir.chmod(0o700)
```

---

## まとめ

Browser-Useのセッション永続化機能により、以下が実現できます：

### ✅ 主要機能

1. **ネイティブプロファイル**: Chromeの標準機能を使用した完全な永続化
2. **Storage State**: JSON形式でのCookie管理
3. **自動保存**: 30秒ごとのポーリング方式
4. **CDP統合**: 暗号化されたCookieの直接取得
5. **セキュア**: chmod 700による権限保護

### ✅ ユースケース

- **長期プロジェクト**: ネイティブプロファイルで全データ永続化
- **CI/CD**: Storage StateをSecrets管理
- **マルチアカウント**: プロファイル分離で並列実行
- **テスト**: 一時的なStorage Stateで再現性確保
- **クロスプラットフォーム**: JSON形式で環境間移行

### ⚠️ セキュリティ

- Storage State JSONには平文でCookieが含まれる → 暗号化推奨
- プロファイルディレクトリは chmod 700 で保護
- 公開リポジトリにStorage Stateをコミットしない
- GitHub Secretsやシークレット管理ツールを活用

---

## 関連ドキュメント

- [Sensitive Data機能](./sensitive_data.md) - LLMから機密情報を隠蔽する機能
- [Browser Profile設定](../../browser_use/browser/profile.py) - プロファイル設定の詳細
- [Config設定](../../browser_use/config.py) - デフォルトパスと権限

---

**最終更新**: 2025-10-21
