# 対旋律機能実装サマリー

## 実装完了内容

フェーズ1（基本構造）とフェーズ2（UI拡張）の両方が完了しました。

### ✓ フェーズ1: 基本構造

#### 1. JSONスキーマの拡張 (`song.json`)
- `mappings` 構造を新スキーマに対応
  - `trigger`: トリガーノート情報（ch, note）
  - `action`: 和音ノート情報（notes, velocity）
  - `countermelody`: **新規** 対旋律設定（enabled, bar_length, sequence）
- 対旋律シーケンスデータ形式
  - `timing_clock`: タイミング（MIDI同期クロック単位）
  - `notes`: 発音するノート配列
  - `velocity`: ベロシティ
  - `duration_clock`: ノート発音期間

#### 2. engine.py拡張

**新クラス: CountermelodyScheduler**
- 対旋律タイミング管理
- クロック単位でのイベント抽出
- ノートのON/OFF自動処理
- 小節単位での自動終了判定

**DuetEngineクラス拡張**
- `countermelody_scheduler`: スケジューラーインスタンス管理
- `countermelody_active_notes`: 発音中の対旋律ノート追跡
- `_start_countermelody()`: 対旋律開始処理
- `_stop_countermelody()`: 対旋律停止・クリーンアップ
- `_update_countermelody()`: クロック毎の更新処理
- `play_action()`: 和音再生時に対旋律も自動開始

**MIDIクロック処理**
- `_run_loop()` 内でクロック毎に対旋律を更新
- 24クロック/4分音符の標準MIDI同期に対応
- 小節トラッキングと独立して動作

### ✓ フェーズ2: UI拡張

#### 新ウィジェット: 対旋律編集ダイアログ

**CountermelodyEditorDialog**
- 対旋律設定UI
  - 有効/無効チェック
  - 小節長セレクタ（1-4）
- ピアノロール風エディタ
  - タイミング（クロック）表示
  - ノート管理
  - ベロシティ制御
  - 発音期間設定

**EventEditorDialog**
- 個別イベント編集
- タイミング、ノート、ベロシティ、期間の入力

**UIメイン画面変更**
- マッピングテーブルに「対旋律編集」ボタン追加
- マッピング編集ダイアログから対旋律へアクセス可能
- 試聴機能で対旋律プレビュー可能

## 技術仕様

### MIDI同期
- **PPQ (Pulses Per Quarter Note)**: 24
- **小節当たりのクロック**: 96（24 × 4拍）
- **対旋律タイミング解像度**: 1クロック（約20ms @ 120BPM）

### データフロー

```
MIDI IN (ELB-02同期クロック)
    ↓
engine._run_loop()
    ├→ clock_countインクリメント
    ├→ _update_countermelody()呼び出し
    │   ├→ CountermelodyScheduler.get_events_at_clock()
    │   ├→ NOTE OFF送信（期限切れノート）
    │   └→ NOTE ON送信（新規イベント）
    └→ 小節進行時にon_bar()コールバック
    ↓
MIDI OUT → シンセサイザー
```

### トリガー時の処理フロー

```
トリガーノート受信
    ↓
DuetEngine.play_action()実行
    ├→ all_notes_off()（前の和音停止）
    ├→ _stop_countermelody()（前の対旋律停止）
    ├→ 和音ノートON送信
    └→ action.countermelodyが有効なら
        └→ _start_countermelody(）
            └→ CountermelodySchedulerをスタート
    ↓
毎クロック：_update_countermelody()で対旋律処理
    ↓
対旋律終了（bar_duration超過）or次トリガー
    → 対旋律ノートOFF
```

## テスト結果

✅ モジュール読み込み成功
```
- song.json: 3マッピング読み込み成功
- [0] Cue_A_minor: CM disabled (0 events)
- [1] Cue_C_major: CM enabled (6 events) ← 対旋律テストサンプル
- [2] Cue_F_major: CM disabled (0 events)
```

✅ CountermelodyScheduler動作確認
```
- バー長: 96クロック（1小節）
- Clock 0: 1 NOTE ON（開始）
- Clock 12: 1 NOTE ON, 1 NOTE OFF（次の音へ）
- Clock 24-60: 各クロックで正常に遷移
```

## ファイル変更概要

### 変更ファイル
- `song.json`: スキーマ拡張 + サンプルデータ
- `engine.py`: +154行（CountermelodyScheduler, 対旋律処理）
- `ui.py`: +240行（対旋律エディタダイアログ）
- `README.md`: ドキュメント拡張（対旋律機能説明）

### 新規ファイル
- `IMPLEMENTATION_SUMMARY.md`: このファイル

## 使用方法

### 対旋律を作成する

1. **マッピングテーブルから行を選択**
2. **「対旋律編集」ボタンをクリック**
3. **対旋律エディタで設定**
   - チェック: 「対旋律を有効にする」
   - 小節長: 1（デフォルト）
   - イベント追加
     - Timing: 0（小節開始）
     - Notes: C4（ノート指定）
     - Velocity: 70
     - Duration: 12（クロック）

4. **「OK」で保存**
5. **「保存」でsong.jsonに保存**

### 演奏時の動作

1. MIDI接続・GM初期化
2. 「開始」でエンジン開始
3. トリガーノート演奏
   - 和音が即座に出力
   - 対旋律有効なら同期クロックで自動演奏
4. 次のトリガーで対旋律停止、新規対旋律開始

## 将来の拡張案

- GUI上でドラッグ&ドロップによるノート配置
- 対旋律プリセット機能
- MIDI Out ポート分離（対旋律を別チャンネル/ポート出力）
- ステップシーケンサー風UI
- 対旋律録音機能（MIDIから自動生成）

## 注意事項

- 対旋律は**トリガー時点のクロック位置から開始**
- クロックが外部からの同期信号である必要がある（ELB-02など）
- bar_lengthを超えたタイミングは自動的に無視される
- 複数の対旋律は同時実行不可（次のトリガーで前の対旋律が停止）
