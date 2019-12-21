- Position and account information will be stored in log.txt, use "flush.bat" in Windows, use "tail -f  log.txt" in Linux.

- 做市策略注：
    - 目前是1.5s每个期权三个价格（会剔除不合理价格）各下29手,能保证80%做市率，但是有一定几率报错
    - 如果出现做市率下降，建议视情况而定调高时间，做市
    - 如果出现错误，建议将时间调高至2s