# README hero GIF: `abp gate` catches a routing regression.
#
# Fully offline — mocked LLM, stubbed tools, no API key.
# Re-record from the repo root (needs `vhs` and an `abp` on PATH whose
# generated-project deps are installed, e.g. one `abp test --install` run):
#
#   vhs docs/assets/gate-demo/gate-demo.tape

Output docs/assets/gate-demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1200
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set WaitTimeout 180s

Hide
Type "cd docs/assets/gate-demo && rm -rf .abp support.yml.bak && export PS1='$ ' && clear"
Enter
Show

Type "# Two scenarios, mocked LLM, stubbed tools — no API key"
Enter
Sleep 400ms
Type "abp test support.yml"
Enter
Wait+Screen /passed/
Sleep 2s

Type "abp gate support.yml --update-baseline"
Enter
Wait+Screen /baseline written/
Sleep 1.5s

Hide
Type "clear"
Enter
Show

Type "# A teammate tightens the routing threshold..."
Enter
Sleep 400ms
Type "sed -i.bak 's/>= 0.8/>= 0.9/' support.yml && diff support.yml.bak support.yml"
Enter
Sleep 2.5s

Type "abp gate support.yml"
Enter
Wait+Screen /Gate (FAILED|PASSED)/
Sleep 1s
Type "echo exit=$?"
Enter
Sleep 4s

Hide
Type "mv support.yml.bak support.yml && rm -rf .abp && cd -"
Enter
