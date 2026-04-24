# MIT 6.S081 / 6.1810 操作系统课程学习仓库

## 项目概览

MIT 操作系统课程（6.S081 2020 / 6.1810 2025）的离线学习环境，包含：
- 汉化版课程官网镜像
- xv6-riscv 内核源码（含个人练习修改）
- 中英文 PDF 阅读材料
- 翻译辅助脚本

## 目录结构

| 目录 | 用途 |
|------|------|
| `source/xv6-riscv/` | xv6 内核源码，**实验和练习在此修改** |
| `source/xv6-riscv/kernel/` | 内核代码（proc, pipe, fs, trap 等） |
| `source/xv6-riscv/user/` | 用户态程序（实验作业写在这里） |
| `official-pages/6.S081-2020/` | 2020 课程官网镜像（已汉化） |
| `official-pages/6.1810-2025/` | 2025 课程官网镜像（已汉化） |
| `readings/` | PDF 阅读材料（xv6 书英/中文版、RISC-V 调用约定） |
| `scripts/` | 翻译和构建脚本（Python） |
| `tmp/code/` | macOS 上的 C 语言练习代码（不进入 xv6） |
| `videos-links/` | 课程视频链接整理 |

## xv6 开发常用命令

```bash
cd source/xv6-riscv

# 启动 QEMU 运行 xv6
make qemu

# 调试模式启动
make qemu-gdb

# 清理构建产物
make clean
```

**工具链依赖**：需要 `riscv64-*-elf-gcc` 和 `qemu-system-riscv64 >= 7.2`

## 用户程序开发（xv6 实验）

新增用户程序步骤：
1. 在 `source/xv6-riscv/user/` 下新建 `yourprog.c`
2. 在 `source/xv6-riscv/Makefile` 的 `UPROGS` 列表中加入 `$U/_yourprog`
3. `make qemu` 后即可在 xv6 shell 中运行

头文件引用规范：
```c
#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"
```

## macOS 本地练习（tmp/code/）

用于在 macOS 上验证 fork/pipe 思路，**不运行在 xv6 内**：

```bash
cd tmp/code
gcc primeSelect.c -o prime && ./prime
gcc pipetest.c -o pipetest && ./pipetest
```

已有练习：
- `primeSelect.c`：Sieve of Eratosthenes，用 fork+pipe 链式筛质数
- `pipetest.c`：父子进程双向管道 ping-pong
- `forkTest.c`：基础 fork + execv 练习

## 翻译脚本

```bash
cd /Users/town/Documents/os

# AI 翻译官方页面（需配置 API Key）
python scripts/translate_official_pages_ai.py

# 构建中文 xv6 书 PDF
python scripts/build_xv6_book_zh_pdf.py
```

脚本使用 `uv` 管理依赖，虚拟环境在 `.venv-translate/`。

## Git 规范

- 提交风格：英文短句，动词开头（参考历史：`Add sleep user command`）
- `tmp/` 目录已在 `.gitignore` 中排除，不提交临时练习
- xv6 源码修改直接在 `source/xv6-riscv/` 内提交

## 关键参考文件

| 我想了解… | 看这里 |
|-----------|--------|
| 进程管理 | `source/xv6-riscv/kernel/proc.c` |
| 管道实现 | `source/xv6-riscv/kernel/pipe.c` |
| 文件系统 | `source/xv6-riscv/kernel/fs.c` |
| 系统调用入口 | `source/xv6-riscv/kernel/syscall.c` |
| 陷阱处理 | `source/xv6-riscv/kernel/trap.c` |
| 用户库 | `source/xv6-riscv/user/ulib.c` |
| xv6 书（中文） | `readings/xv6-book-2020-zh.md` |
| 2020 课程安排 | `official-pages/6.S081-2020/schedule.html` |
