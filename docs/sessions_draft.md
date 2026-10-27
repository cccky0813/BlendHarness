# 多轮测评的标准答案（draft-2026-10-26）

> 由 `eval/render_sessions.py` 从 `eval/sessions_draft.json` 生成，**不要直接编辑这个文件**：改 JSON，再重新生成。
> 这些内容**只有测评脚本和离线裁判能看到**，系统每一轮只收到「用户说」那一句。

**怎么读**：

- **程序检查**：这一轮结束后，程序在物体清单上自动判断的条目。物体名用宽松的模式匹配，`a \| b` 表示名字里有 a 或 b 都算；
- **只允许改动**：这一轮可以新增、修改、删除的物体；其余物体有任何变化，都算**回归**；
- **之后一直要成立**：之后每一轮都会重新检查，用来算**需求保持率**；
- **前置条件**：依赖前面哪些检查。前置没满足时这一轮照常跑，但单独统计；
- **指代**：需求里的「那个……」应该指向哪些物体，用来测指代消解；
- **裁判的问题**：给 LLM 裁判看渲染图回答的是 / 否问题。「依赖 Q1」表示 Q1 答「否」时这一题不再问；P 开头的是「之前的东西有没有被改坏」。

## village（low-poly game scene）

### 第 1 轮

**用户说**：Build a low-poly village on a 10 m x 10 m grass ground: a small cottage (walls, a pitched roof, a door and two windows), a stone well, and two trees.

**程序检查**：

- `v1_house`：存在名字匹配 `cottage \| house \| wall` 的物体
- `v1_roof`：存在名字匹配 `roof` 的物体
- `v1_door`：存在名字匹配 `door` 的物体
- `v1_windows`：`window` 的**实例**至少 2 个
- `v1_well`：存在名字匹配 `well` 的物体
- `v1_trees`：`tree \| trunk` 的**实例**至少 2 个
- `v1_ground`：存在名字匹配 `ground \| grass \| terrain` 的物体

**只允许改动**：任何物体（第一轮）

**之后一直要成立**：`v1_house`；`v1_well`；`v1_ground`

**裁判的问题**：

- **Q1** Is there a small cottage with walls and a pitched roof?
- **Q2** Does the cottage have a visible door and two windows?（依赖 Q1）
- **Q3** Is there a stone well?
- **Q4** Are there two trees?
- **Q5** Do all objects stand on the grass, with nothing floating or sunk?
- **Q6** Is the style consistently low-poly?

### 第 2 轮

**用户说**：Add a small vegetable garden about 3 m east of the cottage, enclosed by a wooden fence, with three rows of vegetables inside.

**程序检查**：

- `v2_garden`：存在名字匹配 `garden \| vegetable \| crop \| plant` 的物体
- `v2_fence`：存在名字匹配 `fence` 的物体
- `v2_east`：`fence \| garden` 在 `cottage \| house` 的东边 1.5–5.0 m
- `v2_rows`：`vegetable \| crop \| cabbage \| carrot \| lettuce \| plant \| row` 的**实例**至少 3 个

**只允许改动**：`garden \| vegetable \| crop \| plant \| fence \| row \| cabbage \| carrot \| lettuce \| soil \| bed`

**之后一直要成立**：`v2_garden`

**裁判的问题**：

- **Q1** Is there a vegetable garden?
- **Q2** Is the garden enclosed by a wooden fence?（依赖 Q1）
- **Q3** Are there three rows of vegetables inside the fence?（依赖 Q1）
- **Q4** Is the garden roughly 3 m away from the cottage, not touching it?（依赖 Q1）
- **P1** Are the cottage, well and trees unchanged from before?

### 第 3 轮

**用户说**：Make the cottage roof red, like clay tiles.

**程序检查**：

- `v3_red_roof`：`roof` 的材质是红色

**只允许改动**：`roof \| tile`

**之后一直要成立**：`v3_red_roof`

**裁判的问题**：

- **Q1** Is the cottage roof red?
- **Q2** Does the roof keep its pitched shape?（依赖 Q1）
- **P1** Are the walls, door, windows, garden, well and trees unchanged?

### 第 4 轮

**用户说**：Add a gravel path from the cottage door to the well.

**程序检查**：

- `v4_path`：存在名字匹配 `path \| gravel \| stone_path \| walkway` 的物体
- `v4_path_door`：`path \| gravel \| walkway` 与 `door` 的间隙 ≤ 0.6 m
- `v4_path_well`：`path \| gravel \| walkway` 与 `well` 的间隙 ≤ 0.6 m
- `v4_path_ground`：`path \| gravel \| walkway` 着地（最低点在地面附近）

**只允许改动**：`path \| gravel \| walkway \| pebble \| stone(?!.*well)`

**之后一直要成立**：`v4_path`

**前置条件**：`v1_door`（存在名字匹配 `door` 的物体）；`v1_well`（存在名字匹配 `well` 的物体）

**裁判的问题**：

- **Q1** Is there a gravel path on the ground?
- **Q2** Does the path start at the cottage door?（依赖 Q1）
- **Q3** Does the path end at the well?（依赖 Q1）
- **Q4** Does the path lie flat on the grass (not floating, not cutting through objects)?（依赖 Q1）
- **P1** Are the cottage (with its red roof), garden, well and trees unchanged?

### 第 5 轮

**用户说**：Remove the fence around the garden, but keep the vegetables.

**程序检查**：

- `v5_no_fence`：**不存在**名字匹配 `fence` 的物体
- `v5_veg_kept`：`vegetable \| crop \| cabbage \| carrot \| lettuce \| plant \| row` 的**实例**至少 3 个

**只允许改动**：`fence \| post \| rail \| picket`

**之后一直要成立**：`v5_no_fence`

**前置条件**：`v2_fence`（存在名字匹配 `fence` 的物体）

**裁判的问题**：

- **Q1** Is the garden fence gone?
- **Q2** Are the three rows of vegetables still there?
- **P1** Are the cottage, red roof, path, well and trees unchanged?

### 第 6 轮

**用户说**：Put a wooden bucket next to the well, touching its outer wall.

**程序检查**：

- `v6_bucket`：存在名字匹配 `bucket \| barrel \| pail` 的物体
- `v6_touch_well`：`bucket \| barrel \| pail` 与 `well` 的间隙 ≤ 0.05 m
- `v6_grounded`：`bucket \| barrel \| pail` 着地（最低点在地面附近）

**只允许改动**：`bucket \| barrel \| pail`

**之后一直要成立**：`v6_touch_well`

**前置条件**：`v1_well`（存在名字匹配 `well` 的物体）

**裁判的问题**：

- **Q1** Is there a wooden bucket?
- **Q2** Is the bucket touching the outside of the well (not inside it, not floating)?（依赖 Q1）
- **Q3** Is the bucket's size reasonable next to the well?（依赖 Q1）
- **P1** Are the cottage, path, vegetables and trees unchanged, and is the fence still gone?

## bedroom（room interior）

### 第 1 轮

**用户说**：Build a bedroom corner: a wooden floor, two walls meeting at a right angle, and a double bed with its headboard against the back wall.

**程序检查**：

- `b1_floor`：存在名字匹配 `floor` 的物体
- `b1_walls`：`wall` 的**实例**至少 2 个
- `b1_bed`：存在名字匹配 `bed \| mattress \| headboard` 的物体
- `b1_headboard_wall`：`headboard` 与 `wall` 的间隙 ≤ 0.1 m

**只允许改动**：任何物体（第一轮）

**之后一直要成立**：`b1_bed`；`b1_headboard_wall`

**裁判的问题**：

- **Q1** Is there a wooden floor with two walls meeting at a right angle?
- **Q2** Is there a double bed?
- **Q3** Is the bed's headboard against the back wall?（依赖 Q2）
- **Q4** Is the bed resting on the floor, not floating or sunk?（依赖 Q2）

### 第 2 轮

**用户说**：Add two matching nightstands on both sides of the bed, each with a small table lamp on top.

**程序检查**：

- `b2_stands`：`nightstand \| night_stand \| bedside` 的**实例**至少 2 个
- `b2_lamps`：`lamp` 的**实例**至少 2 个
- `b2_lamp_on_stand`：`lamp` 放在 `nightstand \| night_stand \| bedside` 上面（底面贴着对方顶面）

**只允许改动**：`nightstand \| night_stand \| bedside \| lamp \| shade \| bulb \| drawer \| knob \| handle`

**之后一直要成立**：`b2_stands`；`b2_lamp_on_stand`

**前置条件**：`b1_bed`（存在名字匹配 `bed \| mattress \| headboard` 的物体）

**裁判的问题**：

- **Q1** Are there two nightstands?
- **Q4** Is there one nightstand on each side of the bed?（依赖 Q1）
- **Q2** Do the two nightstands look the same?（依赖 Q1）
- **Q3** Is there a lamp on top of each nightstand?（依赖 Q1）
- **P1** Are the bed, floor and walls unchanged?

### 第 3 轮

**用户说**：Hang a large framed painting on the wall above the bed, centred over the headboard.

**程序检查**：

- `b3_painting`：存在名字匹配 `painting \| picture \| frame \| canvas \| art` 的物体
- `b3_on_wall`：`painting \| picture \| frame \| canvas \| art` 与 `wall` 的间隙 ≤ 0.05 m
- `b3_above_bed`：`painting \| picture \| frame \| canvas \| art` 整体在 `headboard` 的上方

**只允许改动**：`painting \| picture \| frame \| canvas \| art`

**之后一直要成立**：`b3_on_wall`

**前置条件**：`b1_headboard_wall`（`headboard` 与 `wall` 的间隙 ≤ 0.1 m）

**裁判的问题**：

- **Q1** Is there a framed painting on the wall?
- **Q2** Is the painting above the bed and centred over the headboard?（依赖 Q1）
- **Q3** Is it flat against the wall, not floating away from it or sunk into it?（依赖 Q1）
- **P1** Are the bed, nightstands and lamps unchanged?

### 第 4 轮

**用户说**：Put a rug under the bed that sticks out about half a metre on both sides.

**程序检查**：

- `b4_rug`：存在名字匹配 `rug \| carpet` 的物体
- `b4_rug_flat`：`rug \| carpet` 着地（最低点在地面附近）
- `b4_rug_under_bed`：`rug \| carpet` 的水平范围包住 `bed \| mattress`

**只允许改动**：`rug \| carpet`

**之后一直要成立**：`b4_rug`

**裁判的问题**：

- **Q1** Is there a rug on the floor?
- **Q2** Is the rug under the bed and visible on both sides of it?（依赖 Q1）
- **Q3** Does the rug lie flat on the floor without cutting through the bed or nightstands?（依赖 Q1）
- **P1** Are the bed, nightstands, lamps and painting unchanged?

### 第 5 轮

**用户说**：Make the lamp on the left nightstand a bit taller and change both lampshades to warm yellow.

**程序检查**：

- `b5_shades_yellow`：`shade \| lamp` 的材质是黄色

**只允许改动**：`lamp \| shade \| bulb \| base \| stem \| pole`

**之后一直要成立**：`b5_shades_yellow`

**前置条件**：`b2_lamps`（`lamp` 的**实例**至少 2 个）

**指代**：「the lamp on the left nightstand」→ `lamp`

**裁判的问题**：

- **Q1** Is one lamp now visibly taller than the other?
- **Q2** Is the taller lamp the one on the left nightstand (as seen facing the bed)?（依赖 Q1）
- **Q3** Are both lampshades warm yellow?
- **Q4** Do both lamps still stand on their nightstands?
- **P1** Are the bed, nightstands, painting and rug unchanged?

### 第 6 轮

**用户说**：Swap the painting for a round mirror of about the same width.

**程序检查**：

- `b6_mirror`：存在名字匹配 `mirror` 的物体
- `b6_no_painting`：**不存在**名字匹配 `painting \| canvas` 的物体
- `b6_mirror_wall`：`mirror` 与 `wall` 的间隙 ≤ 0.05 m
- `b6_above_bed`：`mirror` 整体在 `headboard` 的上方

**只允许改动**：`painting \| picture \| frame \| canvas \| art \| mirror \| glass`

**前置条件**：`b3_painting`（存在名字匹配 `painting \| picture \| frame \| canvas \| art` 的物体）

**指代**：「the painting」→ `painting \| picture \| frame \| canvas \| art`

**裁判的问题**：

- **Q1** Is there a round mirror?
- **Q4** Is the mirror on the wall above the bed?（依赖 Q1）
- **Q2** Is the painting gone?
- **Q3** Is the mirror about as wide as the painting was and centred over the headboard?（依赖 Q1）
- **P1** Are the bed, nightstands, yellow lampshades and rug unchanged?

## street_corner（urban street corner）

### 第 1 轮

**用户说**：Build a street corner: an L-shaped sidewalk with a curb around a road intersection, and a streetlight at the corner of the sidewalk.

**程序检查**：

- `s1_sidewalk`：存在名字匹配 `sidewalk \| pavement` 的物体
- `s1_road`：存在名字匹配 `road \| street \| asphalt` 的物体
- `s1_curb`：存在名字匹配 `curb \| kerb` 的物体
- `s1_light`：存在名字匹配 `streetlight \| street_light \| lamp \| light_pole \| lamppost` 的物体

**只允许改动**：任何物体（第一轮）

**之后一直要成立**：`s1_sidewalk`；`s1_road`；`s1_light`

**裁判的问题**：

- **Q1** Is there a sidewalk along a road intersection?
- **Q5** Is the sidewalk L-shaped, wrapping around the corner?（依赖 Q1）
- **Q2** Is there a curb between the sidewalk and the road?（依赖 Q1）
- **Q3** Is there a streetlight at the corner of the sidewalk?
- **Q4** Is the sidewalk raised slightly above the road?（依赖 Q1）

### 第 2 轮

**用户说**：Add a bus stop on the longer side of the sidewalk: a shelter with a roof, a glass back panel and a bench inside.

**程序检查**：

- `s2_shelter`：存在名字匹配 `shelter \| bus_stop \| busstop` 的物体
- `s2_bench`：存在名字匹配 `bench \| seat` 的物体
- `s2_glass`：存在名字匹配 `glass \| panel` 的物体
- `s2_on_sidewalk`：`shelter \| bus_stop \| busstop \| post \| pillar` 放在 `sidewalk \| pavement` 上面（底面贴着对方顶面）

**只允许改动**：`shelter \| bus \| stop \| bench \| seat \| glass \| panel \| roof \| post \| pillar \| sign`

**之后一直要成立**：`s2_shelter`；`s2_on_sidewalk`

**前置条件**：`s1_sidewalk`（存在名字匹配 `sidewalk \| pavement` 的物体）

**裁判的问题**：

- **Q1** Is there a bus shelter on the sidewalk?
- **Q2** Does the shelter have a roof and a glass back panel?（依赖 Q1）
- **Q3** Is there a bench inside the shelter?（依赖 Q1）
- **Q4** Is the shelter on the longer side of the sidewalk and not blocking the road?（依赖 Q1）
- **P1** Are the road, curb, sidewalk and streetlight unchanged?

### 第 3 轮

**用户说**：Put three young trees in round planters along the shorter side of the sidewalk, evenly spaced.

**程序检查**：

- `s3_trees`：`tree \| trunk` 的**实例**至少 3 个
- `s3_planters`：`planter \| pot` 的**实例**至少 3 个
- `s3_spacing`：`planter \| pot` 等距排列（间距变化 ≤ 20%）

**只允许改动**：`tree \| trunk \| foliage \| leaves \| canopy \| planter \| pot \| soil`

**之后一直要成立**：`s3_planters`

**前置条件**：`s1_sidewalk`（存在名字匹配 `sidewalk \| pavement` 的物体）

**裁判的问题**：

- **Q1** Are there three young trees in round planters?
- **Q2** Are they along the shorter side of the sidewalk?（依赖 Q1）
- **Q3** Are they evenly spaced?（依赖 Q1）
- **P1** Are the bus shelter, streetlight, road and sidewalk unchanged?

### 第 4 轮

**用户说**：Add a zebra crossing across the road in front of the bus stop.

**程序检查**：

- `s4_crossing`：`zebra \| crossing \| stripe` 的**实例**至少 4 个
- `s4_on_road`：`zebra \| crossing \| stripe` 放在 `road \| street \| asphalt` 上面（底面贴着对方顶面）

**只允许改动**：`zebra \| crossing \| stripe \| marking`

**之后一直要成立**：`s4_crossing`

**前置条件**：`s2_shelter`（存在名字匹配 `shelter \| bus_stop \| busstop` 的物体）

**裁判的问题**：

- **Q1** Are there white zebra-crossing stripes on the road?
- **Q2** Is the crossing in front of the bus stop?（依赖 Q1）
- **Q3** Do the stripes lie flat on the road surface (not floating, not hidden under it)?（依赖 Q1）
- **P1** Are the bus shelter, trees, streetlight and sidewalk unchanged?

### 第 5 轮

**用户说**：Move the streetlight so it stands next to the zebra crossing instead of at the corner.

**程序检查**：

- `s5_light_near_crossing`：`streetlight \| street_light \| lamp \| light_pole \| lamppost` 与 `zebra \| crossing \| stripe` 的间隙 ≤ 1.5 m
- `s5_light_on_sidewalk`：`streetlight \| street_light \| light_pole \| lamppost \| pole` 放在 `sidewalk \| pavement` 上面（底面贴着对方顶面）

**只允许改动**：`streetlight \| street_light \| lamp \| light_pole \| lamppost \| pole \| bulb \| head`

**之后一直要成立**：`s5_light_on_sidewalk`

**前置条件**：`s1_light`（存在名字匹配 `streetlight \| street_light \| lamp \| light_pole \| lamppost` 的物体）；`s4_crossing`（`zebra \| crossing \| stripe` 的**实例**至少 4 个）

**指代**：「the streetlight」→ `streetlight \| street_light \| lamp \| light_pole \| lamppost`

**裁判的问题**：

- **Q1** Is there a streetlight?
- **Q4** Is the streetlight next to the zebra crossing now?（依赖 Q1）
- **Q2** Is there no longer a streetlight at the sidewalk corner?
- **Q3** Does the streetlight still stand on the sidewalk, not on the road?（依赖 Q1）
- **P1** Are the bus shelter, trees, crossing and sidewalk unchanged?

### 第 6 轮

**用户说**：Replace the middle tree with a red fire hydrant, keeping the other two trees where they are.

**程序检查**：

- `s6_hydrant`：存在名字匹配 `hydrant` 的物体
- `s6_red`：`hydrant` 的材质是红色
- `s6_two_planters`：`planter \| pot` 的**实例**至少 2 个、至多 2 个
- `s6_hydrant_on_sidewalk`：`hydrant` 放在 `sidewalk \| pavement` 上面（底面贴着对方顶面）

**只允许改动**：`hydrant \| tree \| trunk \| foliage \| leaves \| canopy \| planter \| pot \| soil \| cap \| nozzle \| valve`

**前置条件**：`s3_planters`（`planter \| pot` 的**实例**至少 3 个）

**指代**：「the middle tree」→ `tree \| trunk \| planter`

**裁判的问题**：

- **Q1** Is there a red fire hydrant?
- **Q4** Is the hydrant where the middle tree was?（依赖 Q1）
- **Q2** Are the other two trees still in their original places?
- **Q3** Does the hydrant stand on the sidewalk?（依赖 Q1）
- **P1** Are the bus shelter, crossing, moved streetlight and road unchanged?
