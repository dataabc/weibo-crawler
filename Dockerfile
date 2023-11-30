# 设置基础镜像
FROM python:3.12.0-bookworm

# 设置工作目录
WORKDIR /app

# 复制项目文件到工作目录
COPY requirements.txt .

# 安装依赖包
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件到工作目录
COPY . .

# 设置循环间隔
ENV schedule_interval=1

# 运行应用
CMD python __main__.py $schedule_interval