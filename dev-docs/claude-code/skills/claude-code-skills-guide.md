# Claude Code Skills - 完全ガイド

## 概要

Claude Code Skillsは、Claudeの機能を拡張するモジュラー型の能力パッケージです。専門知識を再利用可能な形式にパッケージ化し、プロジェクト間で共有できます。

### Skillsとスラッシュコマンドの違い

| 特性 | Skills | Slash Commands |
|------|--------|----------------|
| 呼び出し方式 | **モデル呼び出し** - Claudeが自律的に判断 | **ユーザー呼び出し** - 明示的に`/command`を実行 |
| 使用タイミング | Claudeが説明文を読んで適切と判断した時 | ユーザーが意図的に実行した時 |
| 用途 | 専門的な処理の自動化 | 定型作業の実行 |

**重要な制約**: Skillsは**モデル呼び出し**です。Claudeに特定のSkillを強制的に使わせることはできません。Skillの発見は`description`フィールドとユーザーのリクエストとのマッチングに依存します。

## コアコンセプト

### Skillの3つの発見メカニズム

1. **Personal Skills** (`~/.claude/skills/`)
	- 全プロジェクトで利用可能
	- 個人用の設定やツール
	- ホームディレクトリに配置

2. **Project Skills** (`.claude/skills/`)
	- gitで共有
	- チーム全体で利用
	- プロジェクトルートに配置

3. **Plugin Skills**
	- Claude Codeプラグインにバンドル
	- マーケットプレイスから配布

### フォルダー階層の詳細

#### Personal Skills の配置

Personal Skillsは**ホームディレクトリ**の`.claude/skills/`配下に配置します。各Skillは**専用のディレクトリ**を持ち、その中に`SKILL.md`を配置します。

```bash
~/.claude/skills/
├── git-commit-helper/          # Skill 1
│   └── SKILL.md
├── pdf-processor/              # Skill 2
│   ├── SKILL.md
│   ├── examples.md
│   └── scripts/
│       └── extract.py
└── financial-calculator/       # Skill 3
    ├── SKILL.md
    ├── reference.md
    └── resources/
        └── formulas.json
```

**重要なポイント**:
- 各Skillは**独立したディレクトリ**に配置
- `SKILL.md`は必ず**各Skillディレクトリの直下**に配置
- ディレクトリ名は`name`フィールドと同じ命名規則（小文字、数字、ハイフン）を使用

**作成手順**:
```bash
# Personal Skillを作成
mkdir -p ~/.claude/skills/my-skill
cat > ~/.claude/skills/my-skill/SKILL.md <<'EOF'
---
name: my-skill
description: Description of what this skill does
---

# My Skill

## Instructions
...
EOF
```

#### Project Skills の配置

Project Skillsは**プロジェクトルート**の`.claude/skills/`配下に配置します。構造はPersonal Skillsと同じです。

```bash
/path/to/your-project/
├── .claude/
│   └── skills/
│       ├── project-specific-tool/    # プロジェクト固有のSkill 1
│       │   └── SKILL.md
│       ├── team-workflow/            # プロジェクト固有のSkill 2
│       │   ├── SKILL.md
│       │   └── scripts/
│       │       └── workflow.sh
│       └── code-reviewer/            # プロジェクト固有のSkill 3
│           ├── SKILL.md
│           └── examples.md
├── src/
├── tests/
└── README.md
```

**重要なポイント**:
- `.claude/`ディレクトリはプロジェクトルートに配置
- gitにコミットして共有
- チームメンバーが`git pull`すると自動的に利用可能

**作成手順**:
```bash
# プロジェクトルートで実行
cd /path/to/your-project

# Project Skillを作成
mkdir -p .claude/skills/my-team-skill
cat > .claude/skills/my-team-skill/SKILL.md <<'EOF'
---
name: my-team-skill
description: Team-specific skill description
---

# My Team Skill

## Instructions
...
EOF

# Gitにコミット
git add .claude/skills/
git commit -m "feat(skills): add my-team-skill"
git push
```

#### 複数Skillの管理

Personal SkillsとProject Skillsを併用することで、汎用的な個人ツールとプロジェクト固有の機能を使い分けられます。

```bash
# 個人用（全プロジェクトで使える汎用的なツール）
~/.claude/skills/
├── git-commit-helper/
│   └── SKILL.md
├── pdf-processor/
│   └── SKILL.md
└── excel-analyzer/
    └── SKILL.md

# プロジェクト用（このプロジェクト専用）
/path/to/project-a/
└── .claude/skills/
    ├── project-a-deployment/
    │   └── SKILL.md
    └── project-a-testing/
        └── SKILL.md

/path/to/project-b/
└── .claude/skills/
    ├── project-b-api-client/
    │   └── SKILL.md
    └── project-b-data-migration/
        └── SKILL.md
```

**推奨される構造**:
- 各Skillは**skillsディレクトリの直下**に配置（フラットな構造）
- Skillディレクトリ名は説明的に（`git-commit-helper`, `pdf-form-filler`など）
- 関連するSkillは名前のプレフィックスで整理（`project-a-*`, `pdf-*`など）

#### SKILL.mdファイルの配置ルール

**必須要件**:
- `SKILL.md`ファイルは必ず**Skillディレクトリの直下**に配置
- ファイル名は**大文字で`SKILL.md`**（公式ドキュメントで一貫して大文字表記）
- YAMLフロントマターは必須（`name`と`description`フィールド）

**検証方法**:
```bash
# Personal Skillsを確認
find ~/.claude/skills -maxdepth 2 -name "SKILL.md" -type f

# Project Skillsを確認
find .claude/skills -maxdepth 2 -name "SKILL.md" -type f 2>/dev/null

# 期待される出力:
# /home/user/.claude/skills/my-skill-1/SKILL.md
# /home/user/.claude/skills/my-skill-2/SKILL.md
# ./.claude/skills/project-skill/SKILL.md
```

### Progressive Disclosure Architecture

Skillsは**段階的開示アーキテクチャ**を採用しています。Claudeはコンテキスト効率を最適化するため、サポートファイル（reference.md、examples.md、scripts/など）を**必要な時のみ**読み込みます。

この仕組みにより、多数のSkillsを定義してもトークン使用量を最小限に抑えられます。

## Skillの作成

### 基本構造

各Skillには`SKILL.md`ファイルが必須で、YAMLフロントマターを含む必要があります:

```yaml
---
name: skill-name
description: What it does and when to use it
---

# Skill Name

## Instructions
...
```

### 命名規則

**nameフィールド:**
- 小文字英数字とハイフンのみ
- 最大64文字
- 例: `pdf-form-filler`, `excel-analyzer`, `commit-msg-generator`

**descriptionフィールド:**
- 最大1024文字
- **機能**と**使用トリガー**の両方を含める
- 具体的なキーワードを含める

#### 良い例・悪い例

❌ **悪い例**: `"Helps with documents"`
- 曖昧すぎる
- トリガーが不明確

✅ **良い例**: `"Extract text/tables from PDFs, fill forms, merge documents. Use when working with PDF files"`
- 具体的な機能を列挙
- トリガーワード（"PDF files"）が明確

### 単一Skillのディレクトリ構成

各Skill内のファイル構成は以下の通りです：

```
skill-name/                     # Skillディレクトリ（名前はname フィールドと一致推奨）
├── SKILL.md                    # ✅ 必須: メインの指示ファイル
├── reference.md                # ⭕ オプション: APIリファレンス、技術詳細
├── examples.md                 # ⭕ オプション: 使用例、サンプルコード
├── scripts/                    # ⭕ オプション: 実行可能スクリプト
│   ├── helper.py               #    Python スクリプト
│   ├── processor.sh            #    Shell スクリプト
│   └── analyzer.js             #    JavaScript スクリプト
├── resources/                  # ⭕ オプション: テンプレート、設定ファイル
│   ├── template.json           #    JSON テンプレート
│   ├── config.yaml             #    設定ファイル
│   └── data.csv                #    サンプルデータ
└── requirements.txt            # ⭕ オプション: Python依存関係（推奨）
```

#### 各ファイルの役割

**1. SKILL.md（必須）**
- Skillのメインファイル
- YAMLフロントマター（`name`, `description`, `allowed-tools`）を含む
- Claudeへの指示を記載
- 最初に読み込まれるファイル

**2. reference.md（オプション）**
- 詳細なAPIドキュメント
- コマンドラインオプションの説明
- パラメータの仕様
- SKILL.mdから`See reference.md for details`のように参照

**3. examples.md（オプション）**
- 実際の使用例
- 入出力のサンプル
- エッジケースの処理方法
- SKILL.mdから`See examples.md for use cases`のように参照

**4. scripts/（オプション）**
- 実行可能なヘルパースクリプト
- Claudeが`Bash`ツールで実行
- 実行権限を付与: `chmod +x scripts/*.py`
- Shebang行を必ず含める: `#!/usr/bin/env python3`

**5. resources/（オプション）**
- テンプレートファイル
- 設定ファイル
- サンプルデータ
- スクリプトから読み込む静的リソース

**6. requirements.txt / package.json（推奨）**
- Python依存関係: `requirements.txt`
- Node.js依存関係: `package.json`
- インストール手順をSKILL.mdに記載

#### Progressive Disclosure の実践例

```
# 最小構成（シンプルなSkill）
git-commit-helper/
└── SKILL.md                    # すべての指示を1ファイルに

# 中規模構成（スクリプト付き）
pdf-processor/
├── SKILL.md                    # メインの指示
├── examples.md                 # 使用例
└── scripts/
    └── extract.py              # PDFテキスト抽出スクリプト

# 大規模構成（フル機能）
financial-calculator/
├── SKILL.md                    # メインの指示
├── reference.md                # 財務比率の計算式詳細
├── examples.md                 # サンプル財務諸表での計算例
├── scripts/
│   ├── calculate_ratios.py     # 比率計算スクリプト
│   └── generate_report.py      # レポート生成スクリプト
├── resources/
│   ├── ratio_definitions.json  # 比率の定義データ
│   └── report_template.html    # HTMLレポートテンプレート
└── requirements.txt            # pandas, numpy等
```

#### Progressive Disclosureの実践

`SKILL.md`には、サポートファイルへの参照を含めることができます:

```markdown
## Instructions

1. Analyze the input data
2. For detailed API documentation, see reference.md
3. For usage examples, see examples.md
4. Use scripts/calculate.py to perform calculations
```

Claudeは必要に応じてこれらのファイルを読み込むため、SKILL.md本体は簡潔に保つことができます。

**ベストプラクティス**:
- `SKILL.md`: 基本的な指示と概要（常に簡潔に）
- `reference.md`: 詳細なAPIやパラメータ仕様
- `examples.md`: 具体的な使用例やサンプルコード
- `scripts/`: 実際の処理を行うヘルパースクリプト

### SKILL.mdの書き方

```markdown
---
name: generating-commit-messages
description: Generate clear commit messages from diffs. Use when writing commits or reviewing staged changes.
---

# Generating Commit Messages

## Instructions

1. Run `git diff --staged` to see staged changes
2. Analyze the diff and suggest a commit message with:
   - Summary line (50 chars or less)
   - Detailed description
   - Affected components

## Best Practices

- Use present tense ("Add feature" not "Added feature")
- Explain what and why, not how
- Reference issue numbers if available
- Use conventional commit format: `type(scope): subject`

## Examples

### Feature Addition
```
feat(auth): add OAuth2 authentication

Implement OAuth2 flow for third-party login.
Includes Google and GitHub providers.

Fixes #123
```

### Bug Fix
```
fix(parser): handle null values in JSON parsing

Previously crashed on null values.
Now returns default empty object.
```
```

## ツールアクセスの制限

`allowed-tools`フィールド（オプション）で、Skillが使用できるツールを制限できます:

```yaml
---
name: safe-file-reader
description: Read and analyze files without modification
allowed-tools: Read, Grep, Glob
---
```

### 動作

- **allowed-tools指定時**: 指定したツールのみ使用可能（**パーミッション要求なし**）
- **allowed-tools未指定時**: すべてのツールにアクセス可能（通常のパーミッション要求あり）

### ユースケース

- **セキュリティ**: 読み取り専用操作に制限（Read, Grep, Globのみ）
- **ワークフロー制御**: 特定の処理に必要なツールのみ許可
- **意図しない変更の防止**: EditやWriteツールを除外

**利用可能なツール名**: Read, Write, Edit, Bash, Grep, Glob, WebFetch など（Claude Codeのツール名）

**プラットフォーム制約**: `allowed-tools`フィールドは**Claude Codeでのみ**サポートされています。

## 設定とベストプラクティス

### 1. 説明文の具体性

曖昧な説明では発見されません。以下を含める:

- **何をするか**: 具体的な機能
- **いつ使うか**: トリガーとなるシナリオ
- **キーワード**: ユーザーが言及する用語

```yaml
# 悪い例
description: Helps with data

# 良い例
description: Analyze Excel sales data, generate pivot tables, and create charts. Use when working with .xlsx files containing sales or revenue data.
```

### 2. Skillのフォーカス

Skillは**狭いスコープ**に保つ:

✅ **良い例**:
- `pdf-form-filling` - PDFフォームの記入に特化
- `excel-data-analysis` - Excelデータ分析に特化

❌ **悪い例**:
- `document-processing` - 広すぎる → 複数のSkillに分割

### 3. チームでのテスト

共有前に検証:
- 適切に起動するか？
- 指示は明確か？
- エッジケースの例は十分か？

### 4. バージョン管理

SKILL.md内にバージョン履歴を記載:

```markdown
## Version History

### v1.2.0 (2025-01-15)
- Added support for multi-page forms
- Improved error handling for corrupted PDFs

### v1.1.0 (2024-12-01)
- Added checkbox field support
- Fixed signature field positioning
```

## 実装例

### シンプルなSkill（単一ファイル）

```yaml
---
name: git-commit-message
description: Generate conventional commit messages from git diff. Use when committing changes or writing commit messages.
---

# Git Commit Message Generator

## Instructions

1. Run `git diff --staged` to view staged changes
2. Analyze the changes and categorize by type:
   - feat: New features
   - fix: Bug fixes
   - docs: Documentation changes
   - refactor: Code refactoring
   - test: Test additions/changes
   - chore: Build process or auxiliary tool changes

3. Generate message following conventional commits:
   ```
   type(scope): subject

   body

   footer
   ```

## Examples

See examples.md for detailed examples.
```

### 複雑なSkill（マルチファイル + ツール制限）

**SKILL.md:**
```yaml
---
name: pdf-form-processor
description: Extract text, fill forms, and merge PDFs. Use when working with PDF files requiring text extraction or form completion.
allowed-tools: Read, Write, Bash, Grep, Glob
---

# PDF Form Processor

## Overview

Process PDF documents including:
- Text extraction
- Form field filling
- Document merging
- Template application

## Prerequisites

Requires `pypdf2` and `reportlab` packages.

## Instructions

See reference.md for detailed API documentation.
See examples.md for common use cases.
```

**reference.md:**
```markdown
# PDF Processing API Reference

## Text Extraction

Use `scripts/extract_text.py`:
```python
python scripts/extract_text.py input.pdf output.txt
```

## Form Filling

Use `scripts/fill_form.py`:
```python
python scripts/fill_form.py template.pdf data.json output.pdf
```
```

**examples.md:**
```markdown
# PDF Processing Examples

## Example 1: Extract Text from Invoice

Input: invoice.pdf
Output: invoice.txt with structured data

## Example 2: Fill Tax Form

Input: tax_form.pdf, taxpayer_data.json
Output: completed_tax_form.pdf
```

**scripts/fill_form.py:**
```python
#!/usr/bin/env python3
from pypdf2 import PdfReader, PdfWriter
import json
import sys

def fill_form(template_path, data_path, output_path):
	reader = PdfReader(template_path)
	writer = PdfWriter()

	with open(data_path) as f:
		data = json.load(f)

	# Form filling logic...

if __name__ == '__main__':
	fill_form(sys.argv[1], sys.argv[2], sys.argv[3])
```

## デバッグ

### Skillが使用されない場合

**チェックリスト:**

1. **ファイルパスを確認**
	```bash
	ls -la ~/.claude/skills/skill-name/SKILL.md
	ls -la .claude/skills/skill-name/SKILL.md
	```

2. **YAML構文を確認**
	- 開始/終了の`---`が正しいか
	- インデントは**スペースのみ**使用（タブは使用不可）
	- フィールド名が正しいか（`name`, `description`）
	- YAMLが無効だとSkillは読み込まれません

3. **説明文の具体性を確認**
	- トリガーキーワードが含まれているか
	- 機能が具体的に記述されているか

4. **デバッグモードで実行**
	```bash
	claude --debug
	```
	読み込みエラーが表示されます。

### スクリプトの問題

1. **実行権限を確認**
	```bash
	chmod +x scripts/*.py
	chmod +x scripts/*.sh
	```

2. **パスの記法**
	- Unixスタイルのスラッシュを使用: `scripts/helper.py`
	- Windowsスタイルは使用しない: `scripts\helper.py`

3. **依存関係を確認**
	```bash
	# Python
	pip install -r requirements.txt

	# Node.js
	npm install
	```

### 複数Skillの競合

**問題**: 複数のSkillが同じトリガーで起動してしまう

**解決策**: 説明文にコンテキスト固有のトリガーを含める

❌ **悪い例**:
- Skill A: `"For data analysis"`
- Skill B: `"For data analysis"`

✅ **良い例**:
- Skill A: `"Sales data in Excel files (.xlsx) - pivot tables, charts, revenue analysis"`
- Skill B: `"Log files and system metrics - parse logs, extract errors, generate reports"`

## チームでの共有

### 推奨方法: プラグインとして配布

マーケットプレイスでの配布が推奨されます。

### 代替方法: プロジェクトリポジトリにコミット

1. **Skillを作成**
	```bash
	mkdir -p .claude/skills/my-skill
	vim .claude/skills/my-skill/SKILL.md
	```

2. **Gitにコミット**
	```bash
	git add .claude/skills/
	git commit -m "feat(skills): add my-skill for [purpose]"
	git push
	```

3. **チームメンバーがpull**
	```bash
	git pull
	```

	即座に利用可能になります（Claude Code再起動不要）。

## 更新と削除

### 更新

1. `SKILL.md`を編集
2. 変更は**次回Claude Code起動時**に反映

プロジェクトSkillの場合:
```bash
git add .claude/skills/skill-name/
git commit -m "refactor(skills): update skill-name instructions"
git push
```

### 削除

1. ディレクトリを削除
	```bash
	rm -rf .claude/skills/skill-name/
	# または
	rm -rf ~/.claude/skills/skill-name/
	```

2. プロジェクトSkillの場合はコミット
	```bash
	git add .claude/skills/
	git commit -m "chore(skills): remove obsolete skill-name"
	git push
	```

## 組み込みSkills（Cookbook）

Claude Cookbookには以下の組み込みSkillsが含まれています。これらは**専門性の高いドキュメント生成タスク**を自動化するためのものです。

**注**: 以下の詳細なユースケースやトリガーキーワードは、公式Cookbookの例から導き出した推測を含みます。実際の動作はClaudeの判断に依存します。

### 1. Excel (xlsx)

**説明**: `"Create and manipulate Excel workbooks with formulas, charts, and formatting"`

**機能**:
- Excelワークブックの作成と編集
- 複雑な数式の自動生成（VLOOKUP、SUMIF、ピボットテーブルなど）
- グラフ・チャートの自動生成（折れ線グラフ、棒グラフ、円グラフ）
- セルの書式設定（色、フォント、罫線、条件付き書式）
- 複数シート間のデータ連携

**詳細なユースケース**:

#### 1. 財務ダッシュボード生成
```
ユーザー: "2024年第4四半期の売上データから財務ダッシュボードを作成して"

Claudeの動作:
- 売上データを読み込み
- 月次推移グラフを生成
- 前年同期比の計算式を追加
- KPI（売上高、利益率、成長率）を視覚化
- 条件付き書式で達成率を色分け
```

#### 2. 予算vs実績分析
```
ユーザー: "予算と実績の差異分析レポートを作成して"

Claudeの動作:
- 予算データと実績データを比較
- 差異（variance）を計算
- 差異率をパーセンテージで表示
- 閾値を超えた項目を赤色でハイライト
- 部門別・月別の集計表を生成
```

#### 3. 投資ポートフォリオ分析
```
ユーザー: "株式ポートフォリオのパフォーマンスレポートを作成"

Claudeの動作:
- 各銘柄のリターンを計算
- ポートフォリオ全体のアセットアロケーションを円グラフで表示
- リスク指標（標準偏差、シャープレシオ）を算出
- 時系列の推移を折れ線グラフで可視化
```

**トリガーキーワード**:
- 「Excel」「.xlsx」「スプレッドシート」
- 「グラフ」「チャート」「ピボットテーブル」
- 「財務レポート」「予算分析」「売上データ」
- 「ダッシュボード」「KPI」

---

### 2. PowerPoint (pptx)

**説明**: `"Generate professional presentations with slides, charts, and transitions"`

**機能**:
- プロフェッショナルなプレゼンテーションスライドの生成
- タイトルスライド、コンテンツスライド、サマリースライドの自動構成
- データからグラフ・チャートを生成してスライドに挿入
- スライド間のトランジション効果
- 一貫したテーマとレイアウトの適用

**詳細なユースケース**:

#### 1. 投資家向けプレゼンテーション
```
ユーザー: "第3四半期の業績報告プレゼンを作成して"

Claudeの動作:
1. タイトルスライド: 会社名、四半期、日付
2. エグゼクティブサマリー: ハイライト（売上、利益、成長率）
3. 財務ハイライト: 売上推移グラフ
4. 事業別業績: 各事業のパフォーマンス比較
5. 今後の展望: 次四半期の計画
6. Q&Aスライド
```

#### 2. 四半期レビュー資料
```
ユーザー: "チーム向けの四半期レビュー資料を作成"

Claudeの動作:
- 達成したマイルストーンをタイムラインで表示
- KPI達成状況を視覚化（メーター形式）
- 課題と改善策をブレットポイントで整理
- 次四半期の目標を明確に提示
```

#### 3. クロスフォーマット変換
```
ユーザー: "Excelの売上データをプレゼン資料に変換"

Claudeの動作:
- Excelからデータを読み込み
- 重要な数値をハイライトしたスライドを生成
- グラフを自動生成してスライドに配置
- ストーリーラインに沿ったスライド構成
```

**トリガーキーワード**:
- 「PowerPoint」「.pptx」「プレゼン」「スライド」
- 「投資家向け」「四半期レポート」「業績報告」
- 「ピッチデック」「提案資料」

---

### 3. PDF (pdf)

**説明**: `"Create formatted PDF documents with text, tables, and images"`

**機能**:
- フォーマットされたPDFドキュメントの生成
- テキスト、テーブル、画像の統合
- ページレイアウトとスタイリング
- ヘッダー・フッターの自動挿入
- 複数ソースからの情報を統合した最終成果物

**詳細なユースケース**:

#### 1. 最終レポート生成
```
ユーザー: "分析結果をPDFレポートにまとめて"

Claudeの動作:
- エグゼクティブサマリー（1ページ目）
- 分析手法の説明（テキスト + 図表）
- データテーブルの挿入（整形済み）
- グラフの配置（適切なサイズとキャプション）
- 結論と推奨事項
- ページ番号とヘッダーの自動挿入
```

#### 2. 複数ソースの統合
```
ユーザー: "ExcelのデータとPowerPointのグラフをPDFレポートに統合"

Claudeの動作:
- Excelから重要データを抽出してテーブル化
- PowerPointのグラフを画像として取り込み
- 一貫したフォーマットで統合
- 目次の自動生成
- セクションごとにページを区切り
```

#### 3. 公式レポート作成
```
ユーザー: "監査報告書をPDF形式で作成"

Claudeの動作:
- 公式フォーマットのテンプレートを適用
- 署名欄と日付を配置
- ウォーターマークや機密表示を追加
- プリント品質の設定
```

**トリガーキーワード**:
- 「PDF」「レポート」「ドキュメント」
- 「最終版」「印刷用」「配布用」
- 「統合」「まとめる」「エクスポート」

---

### 4. Word (docx)

**説明**: `"Generate Word documents with rich formatting and structure"`

**機能**:
- Microsoft Word形式のドキュメント生成
- リッチテキストフォーマット（太字、イタリック、下線、色）
- 構造化されたドキュメント（見出し、段落、リスト）
- テーブルと画像の挿入
- スタイルとテンプレートの適用
- ページ設定とセクション管理

**詳細なユースケース**:

#### 1. ブランドドキュメント生成
```
ユーザー: "会社のブランドガイドラインをWordで作成"

Claudeの動作:
- カバーページ（ロゴ、タイトル、日付）
- 目次の自動生成
- セクションごとに見出しスタイルを適用
- ブランドカラーの説明（色見本テーブル）
- フォントサンプル（実際のフォントで表示）
- 使用例のスクリーンショットを挿入
```

#### 2. プロフェッショナルレポート作成
```
ユーザー: "技術調査レポートをWord形式で作成"

Claudeの動作:
- タイトルページ（著者、日付、バージョン）
- アブストラクト
- 本文（階層的な見出し構造）
- コードブロックの挿入（等幅フォント）
- 図表の挿入と番号付け
- 参考文献リスト
- ページ番号とヘッダー/フッター
```

#### 3. フォーマット変換
```
ユーザー: "Markdownドキュメントを整形されたWordファイルに変換"

Claudeの動作:
- Markdownの見出しをWord見出しスタイルに変換
- リストを適切にインデント
- コードブロックを等幅フォントで表示
- リンクを保持
- 画像を適切なサイズで挿入
```

**トリガーキーワード**:
- 「Word」「.docx」「ドキュメント」
- 「レポート」「提案書」「仕様書」
- 「ブランドガイドライン」「マニュアル」
- 「フォーマット」「整形」

---

### 組み込みSkillsの共通特徴

**1. Progressive Disclosure（段階的開示）**
- 必要な時だけ機能をロード
- トークン効率を最適化

**2. クロスフォーマット連携**
- Excel → PowerPoint: データからスライド生成
- PowerPoint → PDF: プレゼンを配布用PDFに
- Markdown → Word: ドキュメント変換

**3. ビジネスフォーカス**
- 財務レポート自動化
- データ可視化
- プロフェッショナルなドキュメント生成

**4. テンプレート対応**
- 企業ブランドに沿ったスタイル適用
- 一貫したフォーマット維持

### 実際の使い方

```
# Excel Skillが起動する例
ユーザー: "売上データを分析してExcelダッシュボードを作成して"

# PowerPoint Skillが起動する例
ユーザー: "このExcelデータからプレゼン資料を作って"

# PDF Skillが起動する例
ユーザー: "レポートをPDF形式でエクスポートして"

# Word Skillが起動する例
ユーザー: "技術仕様書をWord形式で作成して"
```

**注意**: これらのSkillは**モデルが自動判断**して起動します。ユーザーが明示的に呼び出す必要はありません

## カスタムSkillの実装パターン

### パターン1: 財務比率計算機

```yaml
---
name: financial-ratio-calculator
description: Calculate financial ratios (P/E, ROE, debt-to-equity) from financial statements. Use when analyzing company financials or investment opportunities.
allowed-tools: Read, Bash
---

# Financial Ratio Calculator

## Supported Ratios

1. **Profitability Ratios**
   - Return on Equity (ROE)
   - Return on Assets (ROA)
   - Net Profit Margin

2. **Liquidity Ratios**
   - Current Ratio
   - Quick Ratio

3. **Leverage Ratios**
   - Debt-to-Equity
   - Interest Coverage

## Usage

Provide financial statement data in JSON format:
```json
{
  "revenue": 1000000,
  "net_income": 150000,
  "total_assets": 2000000,
  "total_equity": 1200000,
  "total_debt": 800000
}
```

The script will calculate and display all relevant ratios.
```

### パターン2: ブランドガイドライン

```yaml
---
name: company-brand-guidelines
description: Apply company brand guidelines including colors, fonts, tone of voice. Use when creating marketing materials, presentations, or documents.
allowed-tools: Read
---

# Company Brand Guidelines

## Brand Colors

Primary: #0066CC
Secondary: #FF6600
Accent: #00CC66

## Typography

Headings: Helvetica Neue Bold
Body: Helvetica Neue Regular

## Tone of Voice

- Professional but approachable
- Clear and concise
- Action-oriented
- Avoid jargon

## Usage Examples

See resources/brand_examples.md
```

## セキュリティ考慮事項

1. **ツールアクセスの最小権限原則**
	```yaml
	allowed-tools: Read, Grep  # 読み取り専用
	```

2. **機密情報の扱い**
	- APIキーや認証情報をSKILL.mdにハードコードしない
	- 環境変数を使用: `os.getenv('API_KEY')`

3. **入力検証**
	```python
	def validate_input(data):
		if not isinstance(data, dict):
			raise ValueError("Invalid input format")
		# Further validation...
	```

4. **サンドボックス化**
	- 外部スクリプト実行時は適切な権限設定
	- ユーザー入力を直接コマンドに渡さない

## API要件（プログラマティック使用）

Skillsをプログラムから使用する場合、以下のヘッダーが必要:

```http
anthropic-version: 2023-06-01
anthropic-beta: skills-2025-10-02,pdfs-2024-09-25,files-2025-01-23,code-execution-2025-01-23
```

## トラブルシューティング FAQ

### Q1: Skillが全く起動しない

**確認事項**:
1. ファイル名が`SKILL.md`（大文字）か
2. YAMLフロントマターの構文が正しいか
3. `claude --debug`で読み込みログを確認

### Q2: 意図しないタイミングで起動する

**解決策**:
- 説明文を具体化し、トリガー条件を明確にする
- 他のSkillと説明文が被っていないか確認

### Q3: スクリプトが実行されない

**確認事項**:
1. 実行権限: `ls -l scripts/`
2. shebang行: `#!/usr/bin/env python3`
3. 依存関係: `pip list` / `npm list`

### Q4: チームメンバーのSkillが動かない

**解決策**:
1. git pullを確認
2. Claude Codeを再起動
3. 依存関係をインストール: `pip install -r requirements.txt`

## まとめ

Claude Code Skillsは、以下を実現します:

- **再利用性**: 専門知識のパッケージ化
- **自律性**: Claudeが自動で適切なSkillを選択
- **スケーラビリティ**: チーム全体で共有可能
- **効率性**: トークン使用量の最適化

**次のステップ**:
1. シンプルなSkillを作成してみる（例: git commit message generator）
2. チーム固有のワークフローをSkill化
3. プラグインとして配布を検討

## 参考リンク

- [Claude Code Skills 公式ドキュメント](https://docs.claude.com/en/docs/claude-code/skills)
- [Claude Cookbooks - Skills](https://github.com/anthropics/claude-cookbooks/tree/main/skills)
- [Conventional Commits](https://www.conventionalcommits.org/)
