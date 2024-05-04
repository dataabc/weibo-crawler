FROM python:3.10-slim

WORKDIR /app

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && \
    pip3 install --upgrade pip && \
    pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

# 为了容器一直运行
RUN /bin/echo "Test log at $(date)" >> /tmp/test.log
CMD ["tail", "-f", "/tmp/test.log"]

# 主机创建Cron作业，每5分钟执行一次Python脚本，并将输出重定向到/dev/null
# */5 * * * * docker exec -it weibo_spider_python bash -c "cd /app/ && python3 weibo.py > /dev/null 2>&1"
# 运行一次Python脚本
# docker exec -it weibo_spider_python bash -c "cd /app/ && python3 weibo.py > /dev/null 2>&1"
