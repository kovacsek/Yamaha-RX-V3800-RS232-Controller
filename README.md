# ESPHome config for Yamaha RX-V3800 RS232 Control 

## Notes:
I didn't get the network control to work, so i made this. Works to my satisfaction for RX-V 3800 and supposedly the RX-V1800.

I implemented many control codes, you can add more if you wish. 
* Many of the codes differ between the models, something like the RX-V3900 has different ones for the surround modes and so on. But the protocol for the pre-2010 models should have stayed the same.
* Tip: The IR remote codes are the same as the RS232 codes and their lists are way easier to find and make sense of. Of course I realised this pretty late.

## These parameters are controllable and give feedback:
* Power On/Off: Everything, Main, Zone2, Zone3
* Mute On/Off/Toggle: Main, Zone2, Zone3
* Input Select: Main, Zone2, Zone3
* Volume Level: Main, Zone2
* All Surround Modes
* GUI Menu Navigation
* USB/Internet Radio/Network Menu Navigation

## Prerequisites:
* You need a MAX232 adapter and an ESPHome compatible microcontroller, i used a ESP32.
  * I plugged my adaptor (which has a male connector? the one with pins) directly into the receiver without using a cable. 
  * Flow Control (RTS/CTS) isn't used.
*You also need to enable RS232 in standby in the advanced setup.
  * Power the unit off with the little MASTER Switch.
  * Hold the STRAIGHT button behind the front lid
  * While still holding it, power it on with the MASTER switch.
  * When you see Advanced Setup on the Display, release the STRAIGHT button.
  * Navigate to the RS232 entry with the Input Select knob and change it to Yes with the Program knob
  * Press the bigger On/Off butten to boot normally, or just switch it off and on again. 
* ESPHome, at least to compile and flash the Firmware.
  * Although this works standalone as it has a built in webserver, a home automation server like HomeAssistant gives this project much more sense. 

## AI Disclaimer
I used AI to make this (Codex), but also invested much manual work to get it to this state.
