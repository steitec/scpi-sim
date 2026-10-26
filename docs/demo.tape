# Demo GIF for the README. Render with charmbracelet/vhs (scpi-sim must be on PATH):
#   vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1150
Set Height 560
Set Padding 20
Set TypingSpeed 35ms
Set Theme "Builtin Dark"

Hide
Type "export PS1='$ '; set +m; clear" Enter
Show

Type "scpi-sim serve psu --load 24 & sleep 0.5" Enter
Sleep 1.5s
Type 'scpi-sim query "*IDN?"' Enter
Sleep 1.5s
Type 'scpi-sim query "VOLT 12;CURR 1;:OUTP ON;:MEAS:CURR?;:OUTP:MODE?"' Enter
Sleep 1.8s
Type 'scpi-sim query "CURR 0.25;:MEAS:VOLT?;:OUTP:MODE?"' Enter
Sleep 1.8s
Type 'scpi-sim query "VOLT 40" "SYST:ERR?"' Enter
Sleep 2s
Type "kill -INT $!; wait" Enter
Sleep 1.5s
