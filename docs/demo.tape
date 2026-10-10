# Records docs/demo.gif with https://github.com/charmbracelet/vhs
#
#   export OPENROUTER_API_KEY=sk-or-...
#   vhs docs/demo.tape            # prefix VHS_NO_SANDBOX=true on Ubuntu 24.04+
#
# Makes two real model calls (about a cent). The clip is a Blender Studio Short from their open game DOGWALK.

Output docs/demo.gif

Require mw

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 800
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set WindowBar Colorful

# Off camera: clean prompt, and warm the download + proxy cache so the GIF shows the watch, not yt-dlp.
Hide
Type "export PS1='\[\e[1;35m\]$\[\e[0m\] '; clear"
Enter
Type "mw watch 'https://www.youtube.com/shorts/qoQIoyTUKhQ' -d fast > /dev/null 2>&1; clear"
Enter
Wait@300s /\$\s*$/
Show

Sleep 500ms
Type `mw watch https://www.youtube.com/shorts/qoQIoyTUKhQ -q "what's the joke?" --no-cache`
Sleep 600ms
Enter
Sleep 2s
# Cut the model wait (~10-20s) out of the GIF.
Hide
Wait@180s /\$\s*$/
Show
Sleep 8s
