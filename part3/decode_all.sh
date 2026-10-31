#!/bin/zsh
# Decode the 8 Part 3 recordings one at a time at low priority.
# ROIs (x,y,w,h in 1920x1080 video pixels) are the screen area read off a frame of each video,
# inset a little from the bezel; at 20 cm the screen fills the whole frame.
cd "$(dirname "$0")"
PY=${PY:-python3}
OUT=../../Analysis/Part3/results
typeset -A ROI
ROI=(
  p3_da010_d020 100,100,1720,880
  p3_da050_d020 100,100,1720,880
  p3_da050_d040 400,160,1180,740
  p3_da010_d040 400,140,1200,760
  p3_da010_d080 700,260,600,380
  p3_da050_d080 700,260,600,380
  p3_da010_d120 744,304,368,240
  p3_da050_d120 744,304,368,240
)
for s in p3_da050_d040 p3_da010_d020 p3_da050_d020 p3_da010_d040 p3_da010_d080 p3_da050_d080 p3_da010_d120 p3_da050_d120; do
  nice -n 15 $PY decode_video.py ../../Data/part3/$s.mov --roi ${ROI[$s]} --out-root $OUT 2>&1 | grep -v "decoded "
done
echo ALL_DONE
