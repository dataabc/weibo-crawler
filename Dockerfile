# 设置基础镜像
FROM python:3.12.0-alpine

# 安装 tzdata 设置时区
RUN apk add --no-cache tzdata \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

# 设置工作目录
WORKDIR /app

# 复制项目文件到工作目录
COPY requirements.txt .

# 使用 Aliyun 镜像源加速 pip
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ -U pip \
    && pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 安装依赖包
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件到工作目录
COPY . .

# 设置循环间隔
ENV schedule_interval=1

# 运行应用
CMD python __main__.py $schedule_interval
