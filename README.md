# wechat2rss-utils

## 批量添加文章到[wechat2rss](https://github.com/ttttmr/wechat2rss)

```bash
  # 推荐：通过环境变量传参
  export WECHAT2RSS_BASE_URL='https://your-host.example.com'
  export RSS_TOKEN='your_token_here'
  python3 /root/project/wechat2rss/scripts/batch_add_from_urls.py --input /path/to/urls.txt --dedupe

  # 或显式传参
  python3 /root/project/wechat2rss/scripts/batch_add_from_urls.py \
    --base-url https://your-host.example.com \
    --token your_token_here \
    --input /path/to/urls.txt \
    --dedupe
```

## 参考
https://github.com/ttttmr/wechat2rss