# Initial firmware source audit

Pinned sources are recorded in `sources.json`. These observations are static source findings, not evidence of behavior on the second printer.

1. `Configuration.h` selects `ENDER_3S1_PRO` and `USER_STM32F103`; `platformio.ini` defaults to `STM32F103RET6_creality`. The earlier working-printer report was an F4 build. Do not flash this default merely because both printers say S1 Pro.
2. `Configuration_adv.h` disables `EMERGENCY_PARSER`, `HOST_ACTION_COMMANDS` and `HOST_PROMPT_SUPPORT`, matching the capabilities previously observed on the working printer.
3. `gcode/lcd/M0_M1.cpp` emits a host prompt when enabled and calls `wait_for_user_response`. It also calls `RTS_CommandPause`; host completion must be reconciled with the screen.
4. `feature/host_actions.cpp` resets `host_prompt_reason` before validating the response. For `PROMPT_USER_CONTINUE`, any response clears the wait. A filament response of 1 can disable the runout sensor. These paths cannot be indiscriminately exposed as generic Continue.
5. `feature/e_parser.h` recognizes `M108`, `M112`, `M410` and single-digit `M876 S` responses directly from incoming bytes. It has no prompt identity token or positive response receipt. Native tests must exercise the actual parser source, not a Python reimplementation.
6. `lcd/dwin/lcd_rts.cpp` contains screen-specific controls and error handling. `ErrorKey` can reboot when `errorway == 4`. `ErrorHanding` can enqueue repeated homing attempts after an error. Exposing all screen buttons remotely would inherit these behaviors.
7. The screen branch contains `DWprj.hmi`, `DWprj.tft`, `DisplayConfig.xls`, `TouchConfig.xls`, images and compiled `DWIN_SET` files. A rebuild procedure and redistribution terms still need verification. Do not label it a reproducibly built screen firmware yet.

The first baseline build uses the unmodified F103 default for source reproducibility only. Physical board selection remains open. The existing printer software and hardware have not been accessed or changed.

Stock recovery-package research and outstanding qualification: [recovery.md](recovery.md).
