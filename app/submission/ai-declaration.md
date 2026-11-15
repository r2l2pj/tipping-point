# AI usage declaration (form field "Use of Super Intelligence (SI)")

> **DRAFT — pending approval.** Rules: 2026 Project Submission Guide. SI is allowed, but it must not be used to come up with the idea. Code and data need an acknowledgement in descriptive text and in metadata. Images and video need a visible watermark. Audio needs an acknowledgement in text and in metadata. Generated content must not use NASA branding. The form answer is not shown to judges, but the acknowledgements inside the project are.

[ONE OPEN ITEM FOR LUCAS: the voiceover line — synthetic or recorded.]

> Yes. The team used AI assistants under human direction throughout the hackathon. Tools: Anthropic Claude (Claude Code, Claude Opus models) to plan, write and review code, documentation and submission text and to script the demo recording; Cognition Devin to implement scoped coding tasks; jcode with OpenAI GPT models for research (literature, datasets, official sources) and independent code and science reviews. What the humans did: the team wrote the idea and the method — the Tipping Point Index, the choice of NASA datasets, the replay and radar flood-candidate design — in our own project document (docs/PLAN.md) before any AI-assisted build; a team member directed every task, made every product and wording decision, and reviewed every data source, threshold and reported number (several numbers were withdrawn or corrected after those reviews). Acknowledgements: descriptive text in the repository README ("Use of AI") and commit metadata (Co-Authored-By trailers on AI-assisted commits). Images and video: the app screens and screenshots show real data; the demo video is a screen recording of the real app with text overlays and captions; two explanatory diagrams (what-nisar-sees, future-of-nisar) were drawn as SVG code by an AI assistant from our text and carry a visible "AI-assisted diagram" label; no AI-generated photographs or footage are used. Voiceover: [synthetic or recorded — Lucas]. If synthetic: "The demo voiceover is AI-generated text-to-speech", stated in the video description and in the file metadata.

If the VO is TTS, replace the last sentence with: "The demo voiceover is AI-generated text-to-speech; this is stated in the video description and in the file's metadata (title/comment tag)." Then add the same line to the video description and run `ffmpeg -metadata comment="Voiceover: AI-generated text-to-speech"`.

Where the acknowledgements live:
- This repo: README → "Use of AI". Commit trailers (`Co-Authored-By: Claude …`) are the metadata.
- The merged repo (`tipping-point-data`, app in `app/`): root README and `app/README.md` → "Use of AI".
- Demo video: real app screens only (no SI images or footage), so no watermark is needed in the video.
- Explanatory diagrams (`what-nisar-sees.png`, `future-of-nisar.png`): AI-drawn SVG, visible "AI-assisted diagram" label in the footer of each image.
