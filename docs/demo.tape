# Demo GIF for the README. Render with charmbracelet/vhs:  vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1100
Set Height 520
Set TypingSpeed 40ms

Type "scpi-sim serve psu --load 24 &" Enter
Sleep 1s
Type "echo '*IDN?' | nc -q1 localhost 5025" Enter
Sleep 1.5s
Type "echo 'VOLT 12;CURR 1;:OUTP ON;:MEAS:CURR?' | nc -q1 localhost 5025" Enter
Sleep 1.5s
Type "echo 'CURR 0.25;:MEAS:VOLT?;:OUTP:MODE?' | nc -q1 localhost 5025" Enter
Sleep 1.5s
Type "echo 'VOLT 40' | nc -q1 localhost 5025; echo 'SYST:ERR?' | nc -q1 localhost 5025" Enter
Sleep 2s
Type "kill -INT %1" Enter
Sleep 1.5s
