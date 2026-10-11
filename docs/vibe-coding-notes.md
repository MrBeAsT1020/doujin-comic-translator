# How this was made (vibe coding, as a beginner)

I wanted one thing: **a free tool to translate manga, manhwa and doujin pages**, find the text, translate it, erase the old lettering, draw the new text, and save the result.

I'm not a programmer. Everything here was built by describing what I wanted to Claude (an AI assistant), running what it produced, and sending back what broke or looked wrong.

## The path (it was not a straight line)

1. **Android browser app.** A first version, but it was hard to install and relied on a free AI tier that turned out to be unreliable. Dropped.
2. **Google Colab notebook.** It worked with Google Drive folders, but was clumsy. Dropped.
3. **Windows PC app (this repo).** A local Python web app that runs on my own computer. This stuck.

Then came lots of rounds of: *"this looks messy"* → better text erasing, auto font fitting, a nicer interface, more translators, an API-keys panel, boxes I can drag and resize, a link loader that actually works, and a standalone `.exe`.

## What worked for me

- **Describe the goal, not the code.** "Erase the text but keep the bubble" works better than guessing function names.
- **Send screenshots and the exact error text.** That's how most bugs got fixed.
- **Make it testable.** Small fake pages and a fake website let problems be found before real pages.
- **Ask what is untested.** The AI can't run every real model or website. Knowing what's still unproven saved time.
- **Look at other open-source projects.** Reading how [comic-translate](https://github.com/ogkalu2/comic-translate) erases and fits text gave the best ideas.
- **Keep secrets out of Git.** API keys live in `config.json`, which `.gitignore` blocks.

## What I'd tell another beginner

- Start tiny, get one step working, then add the next.
- Expect the first version to be wrong. Fixing it is the process.
- Be honest in your README about what works and what doesn't.
- Don't upload anyone's copyrighted pages to your repo or screenshots.
