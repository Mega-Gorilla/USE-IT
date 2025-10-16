# Agent Prompt System

## 概要

Browser-UseのAgentは、LLMと対話するために構造化されたプロンプトシステムを使用します。このシステムは、ブラウザの状態、タスク、履歴、ファイルシステムなどを統合し、LLMが適切なアクションを選択できるようにします。

**主要コンポーネント**:
- **SystemPrompt**: システムメッセージテンプレートの管理（3つのモード）
- **AgentMessagePrompt**: ブラウザ状態からユーザーメッセージの構築
- **MessageManager**: プロンプト作成のオーケストレーション、履歴管理、機密データフィルタリング

**場所**: `browser_use/agent/prompt/`

---

## SystemPrompt クラス

### 概要

`SystemPrompt` は、Agentの動作を定義するシステムメッセージテンプレートを管理します。3つのモードがあり、LLMの性能やコスト要件に応じて選択できます。

### テンプレートモード

| モード | 英語テンプレート | 出力フィールド | 用途 |
|--------|-----------------|----------------|------|
| **Standard** | `en/system_prompt.md` | `thinking`, `evaluation_previous_goal`, `memory`, `next_goal`, `action` | デフォルト。詳細な推論が必要なタスク |
| **Flash** | `en/system_prompt_flash.md` | `memory`, `action` | 高速・低コスト。単純なタスク向け |
| **No Thinking** | `en/system_prompt_no_thinking.md` | `evaluation_previous_goal`, `memory`, `next_goal`, `action` | 推論プロセスが不要な場合 |

`language='jp'` を指定すると、同名の日本語テンプレート（例: `jp/system_prompt.md`）が自動的に使用されます。

### 初期化パラメータ

```python
from browser_use.agent.prompt import SystemPrompt

system_prompt = SystemPrompt(
    max_actions_per_step=10,              # ステップあたりの最大アクション数
    override_system_message=None,         # システムプロンプト全体を上書き
    extend_system_message=None,           # システムプロンプトに追加ルールを付与
    use_thinking=True,                    # thinking フィールドを使用するか
    flash_mode=False,                     # Flash モードを使用するか
    language='en',                        # 使用するテンプレート言語（'en' / 'jp'）
)
```

**モード選択ロジック**:
1. `flash_mode=True` → `system_prompt_flash.md`（`language` に応じたサブディレクトリからロード）
2. `use_thinking=False` → `system_prompt_no_thinking.md`
3. デフォルト → `system_prompt.md`

### 言語設定

`SystemPrompt` および `AgentConfig` / `AgentSettings` は `language` パラメータを受け取り、サポートされているテンプレート言語を切り替えます。

- 指定可能な値: `'en'`（既定）, `'jp'`
- 未サポートの値を指定した場合はログ警告を出しつつ `'en'` にフォールバック
- `Agent(..., language='jp')` や `AgentConfig(language='jp')` のように Agent 初期化時に指定可能

### 使用例

#### 例1: デフォルトプロンプト

```python
from browser_use import Agent, ChatOpenAI

agent = Agent(
    task='Find the price of iPhone 15 Pro on Apple website',
    llm=ChatOpenAI(model='gpt-4.1'),
    language='en',  # 既定の英語テンプレート（en/system_prompt.md）
)
# use_thinking=True, flash_mode=False がデフォルト
```

#### 例2: プロンプト拡張

```python
from browser_use import Agent, ChatOpenAI

extend_message = '''
IMPORTANT RULE: Before starting any search task, ALWAYS:
1. First open a new tab
2. Navigate to duckduckgo.com
3. Use DuckDuckGo for all searches instead of Google

This is a strict requirement for all search operations.
'''

agent = Agent(
    task='Search for latest news about AI',
    llm=ChatOpenAI(model='gpt-4.1'),
    extend_system_message=extend_message,
)
```

**`extend_system_message` の利点**:
- 既存のプロンプトルールを保持しつつ、カスタムルールを追加
- ドメイン固有の制約を適用（例: 特定サイトの使用、特定フォーマットでの出力）
- 複数のAgentで共通の拡張ルールを再利用可能

#### 例3: プロンプト完全上書き

```python
from browser_use import Agent, ChatOpenAI

custom_prompt = '''
You are a specialized web scraper for e-commerce price comparison.

Your ONLY job is to:
1. Navigate to the specified product pages
2. Extract: product name, price, availability
3. Call done with structured JSON output

Output format:
{
  "evaluation_previous_goal": "brief status",
  "memory": "tracking info",
  "next_goal": "next step",
  "action": [...]
}

Never deviate from this workflow. Do not browse unrelated pages.
'''

agent = Agent(
    task='Compare iPhone 15 prices on Amazon, Best Buy, and Apple',
    llm=ChatOpenAI(model='gpt-4.1'),
    override_system_message=custom_prompt,
)

#### 例4: 日本語テンプレートを使用

```python
from browser_use import Agent, ChatGoogle

agent = Agent(
    task='Apple公式サイトでiPhone 15 Proの価格を調べてください',
    llm=ChatGoogle(model='gemini-flash-latest'),
    language='jp',  # jp/system_prompt.md が適用される
)
```
```

**`override_system_message` のユースケース**:
- 高度に専門化されたタスク（価格比較、データ抽出など）
- 標準プロンプトの複雑さが不要な場合
- カスタムワークフローの厳密な制御が必要な場合

**警告**: `override_system_message` を使用すると、標準的なブラウザルール、ファイルシステムガイドライン、エラーハンドリングロジックがすべて失われます。自己責任で使用してください。

#### 例5: Flash モード（高速・低コスト）

```python
from browser_use import Agent, ChatGoogle

agent = Agent(
    task='Navigate to example.com and click the login button',
    llm=ChatGoogle(model='gemini-2.0-flash-exp'),  # 高速LLM
    use_thinking=False,
    flash_mode=True,  # system_prompt_flash.md を使用
)

# Flash モードの出力形式:
# {
#   "memory": "Navigated to example.com, login button found at index 5",
#   "action": [{"click": {"index": 5}}]
# }
```

**Flash モードが適している場合**:
- 単純なナビゲーションタスク（クリック、入力、スクロール）
- 高速レスポンスが必要
- コスト削減が優先事項
- 複雑な推論や長期記憶が不要

#### 例6: 推論なしモード

```python
from browser_use import Agent, ChatOpenAI

agent = Agent(
    task='Fill out the contact form on example.com',
    llm=ChatOpenAI(model='gpt-4.1-mini'),
    use_thinking=False,  # thinking フィールドを削除
    flash_mode=False,    # 他のフィールドは保持
)

# No Thinking モードの出力形式:
# {
#   "evaluation_previous_goal": "Successfully navigated to contact page",
#   "memory": "Form has 3 fields: name, email, message",
#   "next_goal": "Fill in the name field",
#   "action": [{"input_text": {"index": 3, "text": "John Doe"}}]
# }
```

**No Thinking モードが適している場合**:
- 推論プロセスの可視化が不要
- トークン使用量を削減したいが、評価・記憶・目標フィールドは保持したい
- 小型LLMでthinkingフィールドが性能を下げる場合

---

## システムプロンプトテンプレートの構造

### Standard モード (`en/system_prompt.md`)

218行の詳細なプロンプトで、以下のセクションで構成されています:

```
<intro>
エージェントの得意分野を説明:
1. 複雑なWebサイトのナビゲーション
2. フォーム送信と対話的なアクション
3. 情報収集と保存
4. ファイルシステムの効果的な使用
5. エージェントループでの効果的な動作
6. 多様なWebタスクの効率的な実行
```

```
<language_settings>
- デフォルト言語: 英語
- ユーザーリクエストと同じ言語で応答
```

```
<input>
各ステップで提供される情報:
1. <agent_history>: 過去のアクションと結果の時系列
2. <agent_state>: タスク、ファイルシステム、TODO、ステップ情報
3. <browser_state>: URL、タブ、インタラクティブ要素、ページ内容
4. <browser_vision>: 要素のバウンディングボックス付きスクリーンショット
5. <read_state>: extract/read_file アクション後の一時データ
```

```
<browser_rules>
80+ ブラウザ操作の詳細ルール:
- インデックス付き要素のみ操作可能
- 調査には新しいタブを開く
- ページ変更後の新要素に注意
- スクロールは必要な場合のみ
- キャプチャ対処戦略
- extract ツールの賢い使用（高コスト注意）
- 入力後のドロップダウン/サジェスト処理
- アクション中断時の再試行ロジック
- フィルタ適用による効率化
- PDF 自動ダウンロード処理
... など
```

```
<file_system>
永続的なファイルシステムガイドライン:
- todo.md: サブタスクのチェックリスト（完了時に更新）
- CSV: カンマを含むセルはダブルクォート使用
- available_file_paths: ダウンロード/ユーザーファイル（読取専用）
- results.md: 長期タスクの結果蓄積
- 10ステップ未満のタスクではファイルシステム不要
```

```
<task_completion_rules>
`done` アクションを呼ぶタイミング:
- タスクが完全に完了したとき
- max_steps に到達したとき
- 継続が絶対に不可能なとき

done アクションの要件:
- success: 完全成功時のみ true
- text: 全ての関連情報を含む
- files_to_display: ファイル添付（例: ["results.md"]）
- done は単独で呼ぶ（他のアクションと組み合わせない）
- 構造化出力リクエストにはスキーマに従う
```

```
<action_rules>
- ステップあたり最大 {max_actions} アクション
- 複数アクションは順次実行
- ページ変更でシーケンス中断
```

```
<efficiency_guidelines>
推奨アクション組み合わせ:
- input + click → フォーム入力と送信を1ステップで
- input + input → 複数フォームフィールドの入力
- click + click → 複数ステップフローのナビゲーション
- scroll(pages=10) + extract → コンテンツロード後にデータ抽出
- ファイル操作 + ブラウザアクション

避けるべき組み合わせ:
- click + navigate（clickの成功確認不可）
- switch + switch（中間状態の確認不可）
- input + scroll（inputの成功確認不可）
```

```
<reasoning_rules>
各ステップで明示的かつ体系的に推論:
- agent_history から進捗とコンテキストを追跡
- 最後の "Next Goal" と "Action Result" を分析
- browser_vision（スクリーンショット）を真実の情報源として使用
- アクション成功/失敗/不確実を明示的に判断
- todo.md が空で複数ステップタスクの場合、計画を生成
- todo.md の完了項目をマーク
- 同じアクションを繰り返している場合はスタック検出
- read_state の一時情報を分析し、必要ならファイルに保存
- ユーザーリクエストと現在の軌道を比較
- done 前に read_file で出力ファイルを検証
... など
```

```
<examples>
良い出力パターンの例:

<todo_examples>
  - ArXiv 論文収集タスクの todo.md 例
  - チェックリスト形式（[ ] / [x]）
  - 明確な目標と進捗追跡

<evaluation_examples>
  - 成功例: "製品ページに移動し情報発見。結果: 成功"
  - 失敗例: "検索バーへの入力失敗。結果: 失敗"

<memory_examples>
  - 具体的な進捗追跡: "5サイト中2サイト訪問済み"
  - 発見データの記録: "Amazon $39.99, eBay $42.00"

<next_goal_examples>
  - 明確な次のアクション: "カートに追加ボタンをクリック"
</examples>
```

```
<output>
必須JSON出力形式:
{
  "thinking": "構造化された推論ブロック（<reasoning_rules>適用）",
  "evaluation_previous_goal": "前アクションの1文評価（成功/失敗/不確実）",
  "memory": "このステップの具体的な記憶と全体進捗（1-3文）",
  "next_goal": "次の即座の目標とアクション（1文）",
  "action": [{"navigate": {"url": "url_value"}}, ...]
}

action リストは空であってはならない。
```

### Flash モード (`en/system_prompt_flash.md`)

33行の最小限プロンプト:

```
<language_settings>
デフォルト: 英語。ユーザーの言語に合わせる。

<user_request>
最終目標。具体的タスク: 各ステップに従う。自由度高: アプローチを計画。

<browser_state>
要素: [index]<type>text</type>。[indexed]のみインタラクティブ。インデント=子要素。*[=新要素。

<file_system>
- PDF は available_file_paths に自動ダウンロード。ファイル読取またはビューアースクロール。
- 永続ファイルシステムで進捗追跡。
- 長期タスク（10ステップ以上）: todo.md 使用。完了時に replace_file_str で更新。
- CSV: カンマにはダブルクォート使用。
- available_file_paths: ダウンロード/ユーザーファイル（読取/アップロードのみ）。

<output>
必須JSON出力形式:
{
  "memory": "前ステップの成功/失敗? タスクに必要な記憶? 次のベストアクションの計画? 次の即座の目標? 複雑度により長さ調整。単純（例: スタートボタンクリック）なら短く。複雑（例: A,B,Cを記憶し後で訪問）なら詳細に。（最大5文）",
  "action": [{"navigate": {"url": "url_value"}}]
}
```

**Flash モードの特徴**:
- 推論・評価・目標フィールドなし
- `memory` に全て統合（最大5文）
- 極小トークン使用
- 処理速度最優先

### No Thinking モード (`en/system_prompt_no_thinking.md`)

214行のプロンプト（Standard から `thinking` フィールドのみ削除）:

```
<output>
必須JSON出力形式:
{
  "evaluation_previous_goal": "前アクションの1文評価（成功/失敗/不確実）",
  "memory": "このステップの具体的な記憶と全体進捗（1-3文）",
  "next_goal": "次の即座の目標とアクション（1文）",
  "action": [{"navigate": {"url": "url_value"}}, ...]
}

action リストは空であってはならない。
```

**No Thinking モードの特徴**:
- `thinking` フィールドを削除し、トークン使用量を削減
- 評価・記憶・目標フィールドは保持（構造化された出力）
- ブラウザルール、ファイルシステム、効率ガイドラインなどの詳細ルールは全て保持

---

## AgentMessagePrompt クラス

### 概要

`AgentMessagePrompt` は、各ステップでLLMに送信するユーザーメッセージを構築します。ブラウザ状態、エージェント履歴、ファイルシステム、タスク情報などを統合し、LLMが適切な判断を下せるようにします。

### 初期化パラメータ

```python
from browser_use.agent.prompt import AgentMessagePrompt

message_prompt = AgentMessagePrompt(
    browser_state_summary,              # BrowserStateSummary
    file_system,                        # FileSystem | None
    agent_history_description=None,     # str | None (履歴テキスト)
    read_state_description=None,        # str | None (extract/read_file結果)
    task=None,                          # str | None
    include_attributes=None,            # list[str] | None (DOM属性)
    step_info=None,                     # AgentStepInfo | None
    page_filtered_actions=None,         # str | None
    sensitive_data=None,                # str | None
    available_file_paths=None,          # list[str] | None
    screenshots=None,                   # list[str] | None
    vision_detail_level='auto',         # Literal['auto', 'low', 'high']
    include_recent_events=False,        # bool
    sample_images=None,                 # list[ContentPartImageParam] | None
)

user_message = message_prompt.get_user_message(use_vision=True)
```

### メッセージ構造

`AgentMessagePrompt.get_user_message()` が生成するメッセージは以下の構造:

```
<agent_history>
<step_1>:
Evaluation of Previous Step: Successfully navigated to homepage
Memory: Loaded amazon.com, search bar visible at index 15
Next Goal: Input search query into search bar
Result:
Navigated to https://amazon.com
</step_1>

<step_2>:
Evaluation of Previous Step: Input successful, suggestions appeared
Memory: Typed "iPhone 15 Pro", dropdown with 5 suggestions visible
Next Goal: Click first suggestion to search
Result:
Input text 'iPhone 15 Pro' into element 15
</step_2>
</agent_history>

<agent_state>
<user_request>
Find the price of iPhone 15 Pro on Amazon
</user_request>

<file_system>
todo.md (230 bytes):
# Task: Find iPhone 15 Pro price

- [x] Navigate to amazon.com
- [x] Search for "iPhone 15 Pro"
- [ ] Find first product listing
- [ ] Extract price information
- [ ] Call done with price

No other files.
</file_system>

<todo_contents>
# Task: Find iPhone 15 Pro price

- [x] Navigate to amazon.com
- [x] Search for "iPhone 15 Pro"
- [ ] Find first product listing
- [ ] Extract price information
- [ ] Call done with price
</todo_contents>

<step_info>
- Current step: 3/10
- Total steps taken: 2
- Steps remaining: 7
</step_info>
</agent_state>

<browser_state>
<url>
https://www.amazon.com/s?k=iPhone+15+Pro
</url>

<open_tabs>
- Tab 0 (current): Amazon.com: iPhone 15 Pro
</open_tabs>

<interactive_elements>
[0]<a>All Departments</a>
[1]<input placeholder="Search Amazon"></input>
[2]<button>Go</button>
[3]<div>Results for "iPhone 15 Pro"</div>
[4]<div>Sort by: Featured</div>
[5]<div>Apple iPhone 15 Pro (128 GB) - Natural Titanium</div>
	[6]<a>Apple iPhone 15 Pro (128 GB) - Natural Titanium</a>
	[7]<img>Product image</img>
	[8]<span>$999.00</span>
	[9]<button>Add to Cart</button>
[10]<div>Apple iPhone 15 Pro Max (256 GB) - Blue Titanium</div>
	[11]<a>Apple iPhone 15 Pro Max (256 GB) - Blue Titanium</a>
	[12]<span>$1,199.00</span>
... more elements
</interactive_elements>

<page_statistics>
- Total interactive elements: 156
- Links: 87
- Buttons: 23
- Inputs: 8
- Iframes: 0
</page_statistics>
</browser_state>

<browser_vision>
[スクリーンショット画像がここに含まれる - use_vision=True の場合]
</browser_vision>

<read_state>
[extract または read_file アクション後の一時データがここに表示される]
</read_state>
```

### 主要メソッド

#### `get_user_message(use_vision: bool = True) -> UserMessage`

ユーザーメッセージを生成します。

**引数**:
- `use_vision`: スクリーンショットを含めるか（デフォルト: True）

**戻り値**: `UserMessage` オブジェクト（テキスト + 画像コンテンツ）

#### `_extract_page_statistics() -> dict[str, int]`

ページ統計を抽出します（内部メソッド）。

**戻り値**:
```python
{
    'Total interactive elements': 156,
    'Links': 87,
    'Buttons': 23,
    'Inputs': 8,
    'Iframes': 0,
    'Text inputs': 5,
    'Textareas': 1,
    'Select dropdowns': 2,
}
```

#### `_get_browser_state_description() -> str`

ブラウザ状態の説明テキストを生成します（内部メソッド）。

#### `_get_agent_state_description() -> str`

エージェント状態の説明テキストを生成します（内部メソッド）。

### 使用例

通常、`AgentMessagePrompt` は `MessageManager` を通じて使用されますが、直接使用することも可能です:

```python
from browser_use.agent.prompt import AgentMessagePrompt
from browser_use.browser.session import BrowserSession

# ブラウザ状態を取得
browser = BrowserSession()
await browser.start()
browser_state = await browser.get_browser_state_summary()

# メッセージプロンプトを作成
message_prompt = AgentMessagePrompt(
    browser_state_summary=browser_state,
    file_system=None,
    task='Navigate to example.com',
    step_info=None,
)

# ユーザーメッセージを取得
user_message = message_prompt.get_user_message(use_vision=True)

print(user_message.text)  # テキスト部分
print(len(user_message.content))  # コンテンツパーツ数（テキスト + 画像）
```

---

## MessageManager クラス

### 概要

`MessageManager` は、プロンプトシステムの中核オーケストレーターです。`SystemPrompt` と `AgentMessagePrompt` を統合し、履歴管理、機密データフィルタリング、メッセージ構築を一元管理します。

### 初期化パラメータ

```python
from browser_use.agent.message_manager.service import MessageManager

message_manager = MessageManager(
    task='Find iPhone price',                    # タスク説明
    system_message=system_prompt.get_system_message(),  # SystemMessage
    file_system=file_system,                     # FileSystem | None
    state=MessageManagerState(),                 # メッセージ状態
    use_thinking=True,                           # thinking フィールド使用
    include_attributes=None,                     # DOM属性リスト
    sensitive_data=None,                         # 機密データ辞書
    max_history_items=None,                      # 履歴項目の最大数
    vision_detail_level='auto',                  # 画像詳細レベル
    include_tool_call_examples=False,            # ツール呼び出し例を含める
    include_recent_events=False,                 # 最近のイベントを含める
    sample_images=None,                          # サンプル画像
)
```

### 主要メソッド

#### `create_state_messages()`

各ステップの状態メッセージを作成します。

```python
message_manager.create_state_messages(
    browser_state_summary=browser_state,
    model_output=agent_output,      # AgentOutput | None
    result=action_results,          # list[ActionResult] | None
    step_info=step_info,            # AgentStepInfo | None
    use_vision='auto',              # bool | Literal['auto']
    page_filtered_actions=None,     # str | None
    sensitive_data=None,            # dict | None
    available_file_paths=None,      # list[str] | None
)
```

**`use_vision` モード**:
- `'auto'`: アクション結果が `include_screenshot` をリクエストした場合のみスクリーンショット含む
- `True`: 常にスクリーンショット含む
- `False`: スクリーンショット含まない

#### `get_messages() -> list[BaseMessage]`

LLMに送信するメッセージリストを取得します。

```python
messages = message_manager.get_messages()
# [SystemMessage, UserMessage (state), UserMessage (context), ...]
```

#### `add_new_task(new_task: str)`

新しいタスクを追加します（follow-up リクエスト）。

```python
message_manager.add_new_task('Now find iPhone 15 Pro Max price too')
# task が更新され、履歴に追加される
```

#### `_filter_sensitive_data(message: BaseMessage) -> BaseMessage`

機密データをプレースホルダーで置換します（内部メソッド）。

```python
# 機密データ設定
message_manager.sensitive_data = {
    'password': 'my_secret_password',
    'api_key': 'sk-abc123xyz',
}

# メッセージ内の機密データが <secret>password</secret> に置換される
```

### 履歴管理

#### `agent_history_description` プロパティ

エージェント履歴の説明を生成します。`max_history_items` で履歴の長さを制限できます。

```python
message_manager = MessageManager(
    task='...',
    system_message=system_message,
    file_system=None,
    max_history_items=10,  # 最大10項目
)

# 15項目の履歴がある場合:
# - 最初の1項目（初期化）
# - "[... 5 previous steps omitted...]" メッセージ
# - 最後の9項目
# 合計: 10項目の実履歴 + 1つの省略メッセージ
```

**履歴項目の構造**:
```
<step_1>:
Evaluation of Previous Step: ...
Memory: ...
Next Goal: ...
Result:
...
</step_1>
```

### 機密データフィルタリング

`MessageManager` は、メッセージ内の機密データ（パスワード、APIキーなど）を自動的にプレースホルダーで置換します。

#### 旧フォーマット（全ドメイン共通）

```python
sensitive_data = {
    'password': 'my_secret_password',
    'api_key': 'sk-abc123xyz',
}

# メッセージ内で:
# "my_secret_password" → "<secret>password</secret>"
# "sk-abc123xyz" → "<secret>api_key</secret>"
```

#### 新フォーマット（ドメイン別）

```python
sensitive_data = {
    'https://example.com': {
        'username': 'john_doe',
        'password': 'secret123',
    },
    'https://api.service.com': {
        'api_key': 'sk-xyz789',
    },
}

# example.com のページで:
# "john_doe" → "<secret>username</secret>"
# "secret123" → "<secret>password</secret>"
# "sk-xyz789" は置換されない（別ドメイン）

# api.service.com のページで:
# "sk-xyz789" → "<secret>api_key</secret>"
# "john_doe", "secret123" は置換されない（別ドメイン）
```

### 使用例

#### 例1: 基本的な使用

```python
from browser_use.agent.message_manager.service import MessageManager
from browser_use.agent.prompt import SystemPrompt
from browser_use.browser.session import BrowserSession

# システムプロンプトを作成
system_prompt = SystemPrompt(
    max_actions_per_step=10,
    use_thinking=True,
)

# メッセージマネージャーを初期化
message_manager = MessageManager(
    task='Find iPhone 15 Pro price on Amazon',
    system_message=system_prompt.get_system_message(),
    file_system=None,
)

# ブラウザ状態を取得
browser = BrowserSession()
await browser.start()
browser_state = await browser.get_browser_state_summary()

# 状態メッセージを作成
message_manager.create_state_messages(
    browser_state_summary=browser_state,
    model_output=None,  # 初回ステップ
    result=None,
    step_info=None,
)

# LLMに送信するメッセージを取得
messages = message_manager.get_messages()

# LLMに送信
response = await llm.get_response(messages)
```

#### 例2: 機密データ付き

```python
message_manager = MessageManager(
    task='Login to example.com and check account balance',
    system_message=system_prompt.get_system_message(),
    file_system=None,
    sensitive_data={
        'https://example.com': {
            'username': 'user@example.com',
            'password': 'secure_password_123',
        },
    },
)

# プロンプトには以下が含まれる:
# "Here are placeholders for sensitive data:
#  ['username', 'password']
#  To use them, write <secret>the placeholder name</secret>"

# LLMの応答例:
# {
#   "action": [
#     {"input_text": {"index": 5, "text": "<secret>username</secret>"}},
#     {"input_text": {"index": 7, "text": "<secret>password</secret>"}}
#   ]
# }

# 実行時に自動的に実際の値に置換される
```

#### 例3: 履歴制限付き

```python
message_manager = MessageManager(
    task='Complex multi-step task',
    system_message=system_prompt.get_system_message(),
    file_system=None,
    max_history_items=5,  # 最大5項目のみ保持
)

# 10ステップ実行後:
# - ステップ1（初期化）
# - "[... 5 previous steps omitted...]"
# - ステップ6-10（最新4項目）
# 合計: 5項目の実履歴

# コンテキストウィンドウを節約しつつ、最新の状態を保持
```

---

## 実践的な使用パターン

### パターン1: ドメイン固有のルール追加

e-commerce価格比較エージェントの例:

```python
extend_message = '''
## E-commerce Price Comparison Rules

1. **Always extract structured data**:
   - Product name
   - Current price
   - Original price (if discounted)
   - Availability status
   - Shipping information

2. **Use filters efficiently**:
   - Before searching, apply relevant filters (brand, price range, rating)
   - This reduces the number of results and speeds up extraction

3. **Handle dynamic pricing**:
   - Some sites show different prices based on location/login
   - If logged out, note "Price may vary when logged in"
   - Always capture the exact timestamp when price was observed

4. **Compare across 3 sites minimum**:
   - Amazon
   - Best Buy
   - Official manufacturer website

5. **Output format**:
   When calling done, use this structure:
   {
     "success": true,
     "text": "Price comparison completed",
     "files_to_display": ["price_comparison.json"]
   }
'''

agent = Agent(
    task='Compare prices for Sony WH-1000XM5 headphones',
    llm=ChatOpenAI(model='gpt-4.1'),
    extend_system_message=extend_message,
)
```

### パターン2: カスタムワークフローの厳密制御

データ抽出パイプラインの例:

```python
custom_prompt = '''
You are a specialized web data extractor for real estate listings.

## Workflow:
1. Navigate to {target_url}
2. Apply filters: {price_range}, {location}, {property_type}
3. For each listing (max 20):
   a. Click listing
   b. Extract: address, price, bedrooms, bathrooms, sqft, description
   c. Screenshot the property
   d. Navigate back to search results
4. Save data to listings.json
5. Call done with files_to_display: ["listings.json"]

## Output Format:
{
  "evaluation_previous_goal": "brief status",
  "memory": "Extracted {n}/{total} listings",
  "next_goal": "next step",
  "action": [...]
}

## Error Handling:
- If captcha appears, retry once after 5 seconds
- If listing page fails to load, skip and continue
- If all listings extracted, proceed to step 5 even if < 20

## CRITICAL: Never deviate from this workflow. Do not browse unrelated pages.
'''

agent = Agent(
    task='Extract real estate listings',
    llm=ChatOpenAI(model='gpt-4.1'),
    override_system_message=custom_prompt,
)
```

### パターン3: Flash モードで高速タスク処理

単純なナビゲーションタスクの例:

```python
# Gmail自動ログインと未読カウント
agent = Agent(
    task='Login to Gmail and tell me how many unread emails I have',
    llm=ChatGoogle(model='gemini-2.0-flash-exp'),
    use_thinking=False,
    flash_mode=True,
    sensitive_data={
        'https://mail.google.com': {
            'email': 'user@gmail.com',
            'password': 'my_password',
        },
    },
)

# Flash モードは:
# - 推論オーバーヘッドなし
# - 高速レスポンス（< 2秒/ステップ）
# - 低コスト（標準モードの1/3）
# - シンプルなタスクに最適
```

### パターン4: Vision制御による最適化

スクリーンショットの戦略的使用:

```python
# ケース1: 常にビジョン使用（視覚的判断が重要）
agent = Agent(
    task='Find and click the blue "Submit" button',
    llm=ChatOpenAI(model='gpt-4.1-vision'),
    use_vision=True,  # 全ステップでスクリーンショット
)

# ケース2: ビジョン不使用（テキストベースのタスク）
agent = Agent(
    task='Extract all product prices from the page',
    llm=ChatOpenAI(model='gpt-4.1'),
    use_vision=False,  # スクリーンショットなし（コスト削減）
)

# ケース3: 自動モード（必要時のみ）
agent = Agent(
    task='Navigate to the checkout page',
    llm=ChatOpenAI(model='gpt-4.1-vision'),
    use_vision='auto',  # screenshot アクション後のみ
)
```

---

## カスタマイズベストプラクティス

### 1. extend_system_message を優先

標準プロンプトの利点を保ちつつカスタマイズ:

```python
# ✅ 良い例: 拡張
extend_message = '''
Additional rules for this task:
- Always use DuckDuckGo for searches
- Save all results to results.md file
- Include timestamps in all log entries
'''

agent = Agent(task='...', extend_system_message=extend_message)

# ❌ 悪い例: 完全上書き（標準ルールを失う）
custom_prompt = '''
You are a search agent. Use DuckDuckGo.
Output: {"action": [...]}
'''

agent = Agent(task='...', override_system_message=custom_prompt)
```

### 2. モードを適切に選択

| タスクの種類 | 推奨モード | 理由 |
|------------|----------|------|
| 複雑な情報抽出 | Standard (thinking) | 詳細な推論が品質向上 |
| 単純なナビゲーション | Flash | 速度とコストの最適化 |
| 中程度の複雑さ | No Thinking | バランスの良いトレードオフ |
| ビジュアル判断 | Standard + use_vision=True | スクリーンショットで正確性向上 |
| テキスト処理 | Standard + use_vision=False | 不要なコストを回避 |

### 3. 機密データは新フォーマット使用

ドメイン別管理で安全性向上:

```python
# ✅ 良い例: ドメイン別
sensitive_data = {
    'https://bank.example.com': {
        'account_number': '1234567890',
        'routing_number': '987654321',
    },
    'https://shop.example.com': {
        'credit_card': '4111-1111-1111-1111',
    },
}

# ✅ さらに良い例: ワイルドカードドメイン
sensitive_data = {
    'https://*.bank.example.com': {  # すべてのサブドメインに適用
        'account_number': '1234567890',
    },
}
```

### 4. 履歴制限で長期タスクを最適化

```python
# 短期タスク（< 10ステップ）
agent = Agent(task='...', max_history_items=None)  # 全履歴保持

# 中期タスク（10-50ステップ）
agent = Agent(task='...', max_history_items=15)  # 最新15項目

# 長期タスク（> 50ステップ）
agent = Agent(task='...', max_history_items=10)  # 最新10項目
# + ファイルシステムで進捗追跡（todo.md, results.md）
```

### 5. カスタムプロンプトのテスト戦略

```python
# 1. 標準プロンプトでベースライン確立
agent_baseline = Agent(task='Find product price', llm=llm)
result_baseline = await agent_baseline.run()

# 2. カスタムプロンプトでテスト
agent_custom = Agent(
    task='Find product price',
    llm=llm,
    extend_system_message='Always use Amazon first',
)
result_custom = await agent_custom.run()

# 3. 比較
print(f'Baseline steps: {result_baseline.number_of_steps()}')
print(f'Custom steps: {result_custom.number_of_steps()}')
print(f'Baseline success: {result_baseline.is_done()}')
print(f'Custom success: {result_custom.is_done()}')

# 4. 複数タスクでA/Bテスト
tasks = ['Task 1', 'Task 2', 'Task 3']
for task in tasks:
    # ベースライン vs カスタムの成功率を比較
```

---

## トラブルシューティング

### 問題1: Agentが同じアクションを繰り返す

**症状**: 5-10ステップで同じクリック/入力を繰り返し、進捗なし

**原因**:
- プロンプトにスタック検出ロジックが不足
- 失敗を認識していない

**解決策**:
```python
extend_message = '''
## Anti-Loop Rules
- If you repeat the same action 3 times without progress, STOP and try alternative approach
- Alternative approaches:
  1. Scroll to reveal more elements
  2. Use search instead of navigation
  3. Try different element (e.g., different button/link with similar text)
  4. Use extract tool to get more page context
- If stuck after 3 alternatives, call done with success=false and explain issue
'''

agent = Agent(task='...', extend_system_message=extend_message)
```

### 問題2: 機密データが漏洩する

**症状**: ログやhistory.jsonにパスワードが平文で記録される

**原因**: `sensitive_data` の設定漏れまたは誤設定

**解決策**:
```python
# ✅ 正しい設定
agent = Agent(
    task='Login to site',
    llm=llm,
    sensitive_data={
        'https://example.com': {
            'username': 'actual_username',
            'password': 'actual_password',
        },
    },
)

# 履歴保存時も自動フィルタリング
agent.save_history('history.json')
# history.json 内: "actual_password" → "<secret>password</secret>"
```

### 問題3: Flash モードでタスク失敗

**症状**: Flash モードでは成功率が低い（30-50%）

**原因**: Flash モードは複雑な推論に不向き

**解決策**:
```python
# ❌ 不適切: 複雑タスクで Flash モード
agent = Agent(
    task='Research top 10 AI papers from arXiv, extract authors and citations',
    llm=llm,
    flash_mode=True,  # 複雑すぎる
)

# ✅ 適切: Flash モードは単純タスクのみ
agent_simple = Agent(
    task='Navigate to arXiv.org and click "Computer Science" category',
    llm=llm,
    flash_mode=True,  # OK
)

# ✅ 複雑タスクには標準モード
agent_complex = Agent(
    task='Research top 10 AI papers from arXiv, extract authors and citations',
    llm=llm,
    use_thinking=True,  # 詳細な推論が必要
    flash_mode=False,
)
```

### 問題4: 画像トークン消費が大きすぎる

**症状**: GPT-4 Vision使用時、1タスクで$5-10のコスト

**原因**: 全ステップでスクリーンショットを送信

**解決策**:
```python
# オプション1: auto モード（必要時のみ）
agent = Agent(
    task='Extract text from page',
    llm=llm,
    use_vision='auto',  # screenshot アクション後のみ
)

# オプション2: 低解像度モード
agent = Agent(
    task='Navigate to checkout',
    llm=llm,
    use_vision=True,
    vision_detail_level='low',  # 'auto', 'low', 'high'
)

# オプション3: ビジョン完全無効
agent = Agent(
    task='Extract all product names',
    llm=llm,
    use_vision=False,  # テキストのみ
)
```

### 問題5: プロンプトが長すぎてコンテキスト制限超過

**症状**: "Context length exceeded" エラー

**原因**: 長い履歴 + 大きなDOM + スクリーンショット

**解決策**:
```python
# 1. 履歴制限
agent = Agent(
    task='...',
    llm=llm,
    max_history_items=10,  # 最新10項目のみ
)

# 2. DOM属性フィルタ
agent = Agent(
    task='...',
    llm=llm,
    include_attributes=['href', 'title'],  # 必要な属性のみ
)

# 3. ビジョン無効化
agent = Agent(
    task='...',
    llm=llm,
    use_vision=False,  # 画像トークン削減
)

# 4. Flash モード（最小プロンプト）
agent = Agent(
    task='...',
    llm=llm,
    flash_mode=True,  # プロンプト33行のみ
)
```

---

## 関連ファイル

### 実装ファイル

- `browser_use/agent/prompt/__init__.py` - SystemPrompt, AgentMessagePrompt 実装（371行）
- `browser_use/agent/message_manager/service.py` - MessageManager 実装（467行）
- `browser_use/agent/prompt/system_prompts/en/system_prompt.md` - 標準プロンプトテンプレート（英語, 218行）
- `browser_use/agent/prompt/system_prompts/en/system_prompt_flash.md` - Flashプロンプトテンプレート（英語, 33行）
- `browser_use/agent/prompt/system_prompts/en/system_prompt_no_thinking.md` - No Thinkingテンプレート（英語, 214行）
- `browser_use/agent/prompt/system_prompts/jp/system_prompt.md` - 標準プロンプトテンプレート（日本語, 218行）
- `browser_use/agent/prompt/system_prompts/jp/system_prompt_flash.md` - Flashプロンプトテンプレート（日本語, 33行）
- `browser_use/agent/prompt/system_prompts/jp/system_prompt_no_thinking.md` - No Thinkingテンプレート（日本語, 214行）

### 例とテスト

- `examples/features/custom_system_prompt.py` - カスタムプロンプトの使用例
- `browser_use/agent/service.py` - Agent実装でプロンプトシステムの統合を確認

### 関連ドキュメント

- `dev-docs/agent/history_manager.md` - 履歴管理システム（AgentHistoryの詳細）
- `dev-docs/agent/README.md` - Agent全体のアーキテクチャ

---

## まとめ

Browser-UseのPrompt Systemは、3層のアーキテクチャで構成されています:

1. **SystemPrompt**: テンプレート選択とカスタマイズ（extend/override）
2. **AgentMessagePrompt**: ブラウザ状態の構造化とメッセージ生成
3. **MessageManager**: 履歴、機密データ、ビジョン制御の統合管理

**主要な設計原則**:
- **モジュール性**: 各コンポーネントが独立して機能
- **柔軟性**: extend/override による段階的カスタマイズ
- **効率性**: Flash モード、履歴制限、ビジョン制御によるコスト最適化
- **安全性**: ドメイン別機密データフィルタリング

**選択ガイドライン**:

| 要件 | 推奨設定 |
|------|---------|
| 複雑な推論タスク | `use_thinking=True` |
| 高速・低コスト | `flash_mode=True` |
| ビジュアル判断 | `use_vision=True` |
| 長期タスク | `max_history_items=10-15` |
| 機密データ | ドメイン別 `sensitive_data` |
| カスタムルール | `extend_system_message` |
| 完全制御 | `override_system_message` (慎重に) |

このプロンプトシステムを理解することで、Browser-Use Agentの動作を深くカスタマイズし、特定のユースケースに最適化できます。
