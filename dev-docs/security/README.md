# Security（セキュリティ）

Browser-Use のセキュリティ関連機能のドキュメント集です。

## ドキュメント一覧

### [Sensitive Data（機密情報保護）](./sensitive_data.md)

パスワード、APIキー、ユーザー名などの機密情報をLLMから完全に隠蔽する機能について解説しています。

**主なトピック:**
- 2層セキュリティアプローチ（フィルタリング + 置換）
- ドメイン固有認証情報の設定方法
- ドメインパターンマッチングの仕様
- 2FA（TOTP）自動生成機能
- セキュリティベストプラクティス
- トラブルシューティング

**対象読者:**
- 機密情報を扱うブラウザ自動化を実装する開発者
- セキュリティ要件の高いプロダクション環境での利用者
- sensitive_data 機能の内部実装を理解したい開発者

### [Session Persistence（セッション永続化）](./session_persistence.md)

ブラウザに保存された認証情報（Cookie、localStorage、sessionStorage）を永続化・再利用する機能について解説しています。

**主なトピック:**
- ネイティブブラウザプロファイル方式（user_data_dir）
- Storage State エクスポート/インポート方式（JSON）
- 自動保存メカニズム（30秒ごと + 変更時）
- CDP統合によるCookie/storage管理
- マルチアカウント管理とCI/CD環境での使用
- セキュアなディレクトリ権限設定

**対象読者:**
- ログイン状態を維持したいブラウザ自動化の開発者
- CI/CD環境でセッションを再利用したい開発者
- 複数アカウントを管理する必要がある開発者

---

## セキュリティ関連の実装ファイル

| コンポーネント | ファイルパス | 説明 |
|--------------|------------|------|
| **Sensitive Data フィルタリング** | `browser_use/agent/message_manager/service.py` | LLMへの入力前に機密情報を除去 |
| **Sensitive Data 置換** | `browser_use/tools/registry/service.py` | アクション実行時にプレースホルダーを実際の値に置換 |
| **ドメインパターンマッチング** | `browser_use/utils.py` | 安全なURLパターンマッチング |
| **Security Watchdog** | `browser_use/browser/watchdogs/security_watchdog.py` | ドメイン制限の強制と違反検出 |
| **Browser Profile** | `browser_use/browser/profile.py` | allowed_domains などのセキュリティ設定 |

---

## セキュリティテスト

| テストファイル | カバー範囲 |
|-------------|----------|
| `tests/ci/test_agent_sensitive_data.py` | sensitive_data 機能の全体テスト |
| `tests/ci/test_browser_watchdog_security2.py` | SecurityWatchdog のドメイン制限テスト |
| `tests/ci/test_browser_watchdog_security_ip_blocking.py` | SecurityWatchdog のIPブロッキングテスト |
| `tests/ci/test_browser_profile_disable_security.py` | セキュリティ機能無効化のテスト |

---

## クイックスタート

### 最小限の安全な設定

```python
from browser_use import Agent, BrowserProfile

# ドメイン固有の認証情報
sensitive_data = {
	'https://trusted-site.com': {
		'username': 'my_user',
		'password': 'my_password'
	}
}

# ドメイン制限付きブラウザプロファイル
browser_profile = BrowserProfile(
	allowed_domains=['trusted-site.com']
)

# Agent作成
agent = Agent(
	task='Login to trusted-site.com',
	llm=llm,
	sensitive_data=sensitive_data,
	browser_profile=browser_profile
)

await agent.run()
```

### 最高レベルのセキュリティ設定

```python
from browser_use import Agent, BrowserProfile
from langchain_openai import ChatAzureOpenAI

# Azure OpenAI (データロギングなし)
llm = ChatAzureOpenAI(
	api_version='2024-10-21',
	azure_endpoint='https://your-resource.openai.azure.com',
	deployment_name='gpt-4o'
)

# ドメイン制限 + 拡張機能無効化
browser_profile = BrowserProfile(
	allowed_domains=['*.trusted-domain.com'],
	enable_default_extensions=False
)

# 機密情報設定
sensitive_data = {
	'https://*.trusted-domain.com': {
		'api_key': 'secret_key',
		'bu_2fa_code_auth': 'TOTP_SECRET_BASE32'
	}
}

# 最高レベルのプライバシー設定
agent = Agent(
	task='Perform secure operations',
	llm=llm,
	browser_profile=browser_profile,
	sensitive_data=sensitive_data,
	use_vision=False  # スクリーンショット無効化
)

await agent.run()
```

---

## 今後の追加予定

- [ ] スクリーンショット自動マスキング機能
- [ ] Vault統合（HashiCorp Vault, AWS Secrets Manager など）
- [ ] 監査ログ機能（機密情報アクセスの追跡）
- [ ] 認証情報の有効期限管理

---

**最終更新**: 2025-10-17
