"""Gemini TTS で商談音声のサンプルを生成する検証スクリプト。

文字起こし（CLAUDE.md 3.4 の未確定事項）を検証するには実際の商談音声が要るが、
本物の商談録音は持ち出せない。そのため合成音声でサンプルを用意する。
"""

from pathlib import Path

from google.cloud import texttospeech

MODEL = "gemini-3.1-flash-tts-preview"
LANGUAGE = "ja-JP"

# 出力は wav（.gitignore 済み）。cwd に散らからないようスクリプト隣に固定する
OUTPUT_PATH = Path(__file__).parent / "output" / "test_sales_dialogue.wav"

PROMPT = """
これは日本企業で行われている法人営業の商談です。

Salesは30代男性の法人営業担当者です。
落ち着いて丁寧で、顧客の話をよく聞きます。
台本の朗読ではなく、実際の商談のような自然な話し方にしてください。
話す速度は普通で、信頼感のある口調にしてください。

Customerは40代から50代の男性の業務部長です。
自社の業務については詳しい一方で、
ITやシステムについての専門家ではありません。
話す速度は普通で、落ち着いた自然な口調にしてください。

二人とも過度に演技的にはせず、
実際に会議室で対面して会話しているようにしてください。
"""

TEXT = """
Sales: 前回はありがとうございました。前回のお話では、今の販売管理システムをもう10年近く使われていて、そろそろ入れ替えも検討したい、ということでしたよね。
Customer: そうですね。古いですし、社員からも使いづらいという声が出ています。
Sales: ありがとうございます。今日は製品のお話に入る前に、今の業務をもう少し詳しく伺えればと思っています。使いづらいというところで、直近で特に困ったことって何かありましたか？
Customer: 直近ですか。先週ですね。大きめの注文が重なった日があって、その日は営業事務がかなりバタバタしていました。
"""


client = texttospeech.TextToSpeechClient()

synthesis_input = texttospeech.SynthesisInput(
    text=TEXT,
    prompt=PROMPT,
)

multi_speaker_config = texttospeech.MultiSpeakerVoiceConfig(
    speaker_voice_configs=[
        texttospeech.MultispeakerPrebuiltVoice(
            speaker_alias="Sales",
            speaker_id="Achird",
        ),
        texttospeech.MultispeakerPrebuiltVoice(
            speaker_alias="Customer",
            speaker_id="Algieba",
        ),
    ]
)

voice = texttospeech.VoiceSelectionParams(
    language_code=LANGUAGE,
    model_name=MODEL,
    multi_speaker_voice_config=multi_speaker_config,
)

audio_config = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
    sample_rate_hertz=22050,
)

response = client.synthesize_speech(
    input=synthesis_input,
    voice=voice,
    audio_config=audio_config,
)

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_bytes(response.audio_content)

print(f"生成完了: {OUTPUT_PATH}")
