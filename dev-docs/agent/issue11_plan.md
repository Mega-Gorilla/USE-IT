# Issue #11 計画メモ

## 実装範囲
- `SystemPrompt` に `language` パラメータを追加し、`en/`, `jp/` サブディレクトリからテンプレートをロードする。
- `AgentConfig` / `AgentSettings` / `Agent.__init__` に `language` を貫通させ、明示指定および既存設定経由の両方で SystemPrompt に反映する。
- サポート言語のバリデーション（例: `Literal['en', 'jp']`）とフォールバック戦略を設計する。
- ドキュメント (`dev-docs/agent/prompt_system.md` など) を日本語テンプレート対応へ更新する。
- `examples/` とテスト (`tests/unit`) に日本語プロンプト利用シナリオを追加する。

## 留意点
- 既存ユーザーへの影響を考慮し、既定値は英語 (`language='en'`) を維持する。
- 将来的な多言語拡張を見越して、`SUPPORTED_LANGUAGES` の定義や構造を検討しておく。
- 日本語テンプレートファイルは `browser_use/agent/prompt/system_prompts/jp/` 配下を利用し、冗長な `_jp` 付きファイルは整理する。
