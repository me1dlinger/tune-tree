FROM python:3.13.13-slim

# 安装依赖
RUN apt-get update && apt-get install -y \
  supervisor  \
  && rm -rf /var/lib/apt/lists/*

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime

# 创建目录结构
RUN mkdir -p /app/tune-tree

# 复制脚本和API文件
COPY /python  /app/tune-tree
COPY conf/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

RUN chmod +x /app/tune-tree/app.py 

RUN pip install --no-cache-dir -r /app/tune-tree/requirements.txt  

EXPOSE 5000

# 启动命令
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
