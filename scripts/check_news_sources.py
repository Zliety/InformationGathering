"""Check source discovery and up to five original pages without a model or delivery."""
from datetime import datetime
import html
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import web_sources as web


def main():
    selected = os.environ.get('SOURCE_ID', 'both')
    if selected not in ('both', 'dahecube', 'sina_cj'):
        raise ValueError('Unsupported source selection')
    sources = [source for source in web.load_sources(ROOT / 'config/news_sources.json')
               if source['id'] in (('dahecube', 'sina_cj') if selected == 'both' else (selected,))]
    today = datetime.now(web.BEIJING).date()
    report = ['# 新闻来源采集检查', '',
              '只检查公开列表及最多 5 篇原文，不调用 AI、不推送、不修改历史。',
              '成功仅说明抽样可提取；不保证所有文章可用，也不代表文章符合企业选题。', '']
    failed = False
    for source in sources:
        report += [f'## {source["name"]}', '']
        try:
            items = web.discover(source, {'source_timeout': 15}, today)
            report += [f'发现候选文章：{len(items)}', '']
            if not items:
                raise web.SourceError('No valid article URLs passed the configured filters')
        except web.SourceError as exc:
            report += [f'列表检查失败：{exc}', '']
            print(f'{source["id"]}: discovery failed: {exc}', flush=True)
            failed = True
            continue
        usable = False
        for candidate in items[:5]:
            url = candidate['url']
            try:
                data, final_url = web.fetch_public(url, source['allowed_domains'], 15)
                document = web.extract_document(data, final_url, candidate, today)
                print(f'{source["id"]}: original page extracted: {final_url}', flush=True)
                report += [f'抽样原文：[打开页面]({final_url})', '']
                if document is None:
                    # extract_document verifies title/body/date before rejecting age.
                    report += ['标题、正文和日期可解析，但发布时间不在今天至前两天范围内，正式任务不会采用。', '']
                else:
                    report += [f'发布日期：{document["published_date"]}；正文字符数：{len(document["text"])}', '',
                               '<pre>' + html.escape(document['title']) + '\n\n'
                               + html.escape(document['text'][:500]) + '</pre>', '']
                usable = True
                break
            except web.SourceError as exc:
                print(f'{source["id"]}: article failed: {exc} | {url}', flush=True)
                report += [f'抽样失败：[原文]({url}) — {exc}', '']
        if not usable:
            failed = True
            report += ['本次最多 5 篇抽样均未通过原文提取，需进一步适配。', '']
        else:
            report += ['抽样采集通过；正式运行仍会执行日期、AI 选题与历史排重。', '']
    output = '\n'.join(report) + '\n'
    Path('source-check.md').write_text(output, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(output)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
