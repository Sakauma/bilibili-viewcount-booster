import sys
import threading
import random
from queue import Queue
from time import sleep
from typing import Optional
from datetime import date, datetime, timedelta

import requests
from requests.exceptions import RequestException
from fake_useragent import UserAgent

# ================= 参数配置 =================
timeout = 3  # 代理连接超时时间（秒）
thread_num = 75  # 筛选可用代理的并发线程数
boost_thread_num = 15  # 并发刷播放量的线程数
round_time = 305  # 每轮刷量的总时间（秒）
update_pbar_count = 50  # 每处理多少个代理更新一次进度条
bv = sys.argv[1]  # 目标视频的BV号
target = int(sys.argv[2])  # 目标播放量

# ================= 统计状态参数 =================
successful_hits = 0  # 成功发送请求的代理数量
initial_view_count = 0  # 初始播放量
current = 0 # 当前播放量
reach_target = False  # 是否达到目标播放量的标志位
info = {} # 视频信息缓存
stats_lock = threading.Lock()  # 线程锁


# ================= 代理抓取模块 =================
def fetch_from_checkerproxy(min_count: int = 100, max_lookback_days: int = 7) -> list[str]:
    day = date.today()
    for _ in range(max_lookback_days):
        day = day - timedelta(days=1)
        proxy_url = f'https://api.checkerproxy.net/v1/landing/archive/{day.strftime("%Y-%m-%d")}'
        print(f'getting proxies from {proxy_url} ...')
        try:
            response = requests.get(proxy_url, timeout=timeout)
            response.raise_for_status()
        except RequestException as err:
            print(f'checkerproxy unavailable: {err}')
            continue

        data = response.json()
        proxies_obj = data['data']['proxyList']
        if isinstance(proxies_obj, list):
            total_proxies = proxies_obj
        elif isinstance(proxies_obj, dict):
            total_proxies = [proxy for proxy in proxies_obj.values() if proxy]
        else:
            raise TypeError(f'Unexpected type of $.data.proxyList: {type(proxies_obj)}')

        if len(total_proxies) >= min_count:
            print(f'successfully get {len(total_proxies)} proxies from checkerproxy')
            return total_proxies
        print(f'only have {len(total_proxies)} proxies from checkerproxy')
    return []

def fetch_from_proxyscrape() -> list[str]:
    proxy_url = ('https://api.proxyscrape.com/v2/?request=getproxies&protocol=http'
                 '&timeout=2000&country=all')
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxyscrape')
    return proxies

def fetch_from_proxylistdownload() -> list[str]:
    proxy_url = 'https://www.proxy-list.download/api/v1/get?type=http'
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxy-list.download')
    return proxies

def fetch_from_geonode(limit: int = 300) -> list[str]:
    proxy_url = 'https://proxylist.geonode.com/api/proxy-list'
    params = {'limit': limit, 'page': 1, 'sort_by': 'lastChecked', 'sort_type': 'desc', 'protocols': 'http'}
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, params=params, timeout=timeout + 2)
    response.raise_for_status()
    data = response.json().get('data', [])
    proxies = [f"{item['ip']}:{item['port']}" for item in data if item.get('ip') and item.get('port')]
    print(f'successfully get {len(proxies)} proxies from geonode')
    return proxies

def fetch_plaintext_proxy_list(url: str, label: str) -> list[str]:
    print(f'getting proxies from {url} ...')
    response = requests.get(url, timeout=max(timeout, 5))
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip() and ':' in line]
    print(f'successfully get {len(proxies)} proxies from {label}')
    return proxies

def fetch_from_speedx() -> list[str]:
    return fetch_plaintext_proxy_list('https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt', 'TheSpeedX GitHub list')

def fetch_from_monosans() -> list[str]:
    return fetch_plaintext_proxy_list('https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt', 'monosans GitHub list')

def fetch_from_proxifly_cn() -> list[str]:
    url = 'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/CN/data.json'
    print(f'getting proxies from {url} ...')
    try:
        response = requests.get(url, timeout=timeout + 2)
        response.raise_for_status()
        data = response.json()
        proxies = [f"{item['ip']}:{item['port']}" for item in data if item.get('ip') and item.get('port')]
        print(f'successfully get {len(proxies)} proxies from proxifly CN')
        return proxies
    except Exception as e:
        print(f'proxifly CN unavailable: {e}')
        return []

def get_total_proxies() -> list[str]:
    fetchers = [
        ('proxifly_cn', fetch_from_proxifly_cn),
        ('checkerproxy', fetch_from_checkerproxy),
        ('proxyscrape', fetch_from_proxyscrape),
        ('proxy-list.download', fetch_from_proxylistdownload),
        ('geonode', fetch_from_geonode),
        ('speedx', fetch_from_speedx),
        ('monosans', fetch_from_monosans),
    ]
    all_proxies: set[str] = set()
    for name, fetcher in fetchers:
        try:
            proxies = fetcher()
        except RequestException as err:
            print(f'{name} source failed: {err}')
            continue
        except Exception as err:
            print(f'{name} source error: {err}')
            continue
        for proxy in proxies:
            all_proxies.add(proxy)
        if len(all_proxies) >= 500:
            break
    if all_proxies:
        print(f'collected {len(all_proxies)} proxies from available sources')
        return list(all_proxies)
    raise RuntimeError('failed to fetch proxies from all sources')

def build_view_params(video_id: str) -> dict[str, str]:
    normalized = video_id.strip()
    if not normalized:
        raise ValueError('video id is empty')
    lowered = normalized.lower()
    if lowered.startswith('av'):
        aid = normalized[2:]
        if not aid.isdigit():
            raise ValueError(f'invalid av id: {video_id}')
        return {'aid': aid}
    if normalized.isdigit():
        return {'aid': normalized}
    return {'bvid': normalized}

def fetch_video_info(video_id: str) -> dict:
    params = build_view_params(video_id)
    response = requests.get(
        'https://api.bilibili.com/x/web-interface/view',
        params=params,
        headers={'User-Agent': UserAgent().random},
        timeout=timeout + 2
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('code') != 0 or 'data' not in payload:
        msg = payload.get('message', 'unknown error')
        raise RuntimeError(f'bilibili API error: code={payload.get("code")} message={msg}')
    data = payload['data']
    if not data.get('aid') or not data.get('bvid'):
        raise RuntimeError('video info missing key identifiers')
    return data

def time(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    else:
        return f'{int(seconds / 60)}min {seconds % 60}s'

def pbar(n: int, total: int, hits: Optional[int], view_increase: Optional[int]) -> str:
    progress = '━' * int(n / total * 50)
    blank = ' ' * (50 - len(progress))
    if hits is None or view_increase is None:
        return f'\r{n}/{total} {progress}{blank}'
    else:
        return f'\r{n}/{total} {progress}{blank} [Hits: {hits}, Views+: {view_increase}]'

def boost_view_worker(proxy_queue: Queue, video_info: dict, bvid: str, 
                     total_proxies: int, target: int, initial_count: int) -> None:
    """并发刷播放量的工作线程"""
    global successful_hits, current, reach_target, info
    processed_count = 0
    
    while True:
        with stats_lock:
            if reach_target:
                break
        try:
            item = proxy_queue.get(timeout=1)
            proxy, proxy_index = item
            
            sleep(random.uniform(0.1, 0.3))
            
            with stats_lock:
                processed_count += 1
                should_update = (processed_count % update_pbar_count == 0)
            
            if should_update:
                try:
                    new_info = fetch_video_info(bv)
                    with stats_lock:
                        info = new_info
                        current = info['stat']['view']
                        if current >= target:
                            reach_target = True
                except:
                    pass
            
            with stats_lock:
                if reach_target:
                    proxy_queue.task_done()
                    break
            try:
                requests.post('https://api.bilibili.com/x/click-interface/click/web/h5',
                              proxies={
                                  'http': 'http://' + proxy,
                                  'https': 'http://' + proxy 
                              },
                              headers={'User-Agent': UserAgent().random},
                              timeout=timeout,
                              data={
                                  'aid': video_info['aid'],
                                  'cid': video_info['cid'],
                                  'bvid': bvid,
                                  'part': '1',
                                  'mid': video_info['owner']['mid'],
                                  'jsonp': 'jsonp',
                                  'type': video_info['desc_v2'][0]['type'] if video_info['desc_v2'] else '1',
                                  'sub_type': '0'
                              })
                
                with stats_lock:
                    successful_hits += 1
                    hits = successful_hits
                    view_increase = current - initial_count
                
                print(f'{pbar(current, target, hits, view_increase)} proxy({proxy_index+1}/{total_proxies}) success   ', end='')
            except: 
                with stats_lock:
                    hits = successful_hits
                    view_increase = current - initial_count
                print(f'{pbar(current, target, hits, view_increase)} proxy({proxy_index+1}/{total_proxies}) fail      ', end='')
            
            proxy_queue.task_done()
                    
        except: 
            if proxy_queue.empty():
                break
            continue

if __name__ == "__main__":
    # 抓取代理
    print()
    total_proxies = get_total_proxies()

    if len(total_proxies) > 10000:
        print('more than 10000 proxies, randomly pick 10000 proxies')
        random.shuffle(total_proxies)
        total_proxies = total_proxies[:10000]

    # 筛选活跃代理
    active_proxies = []
    count = 0
    def filter_proxys(proxies: 'list[str]') -> None:
        global count
        for proxy in proxies:
            count = count + 1
            try:
                # 测试B站的HTTPS接口，并配置正确的代理路由
                requests.get('https://api.bilibili.com/x/web-interface/view?bvid=BV1gj411x7h7', 
                             proxies={
                                 'http': 'http://'+proxy,
                                 'https': 'http://'+proxy  # 防止本机IP直连测试
                             },
                             headers={'User-Agent': UserAgent().random},
                             timeout=5)
                active_proxies.append(proxy)
            except: 
                pass
            print(f'{pbar(count, len(total_proxies), hits=None, view_increase=None)} {100*count/len(total_proxies):.1f}%   ', end='')

    start_filter_time = datetime.now()
    print('\nfiltering active proxies using bilibili API ...')
    thread_proxy_num = len(total_proxies) // thread_num
    threads = []
    for i in range(thread_num):
        start = i * thread_proxy_num
        end = start + thread_proxy_num if i < (thread_num - 1) else None
        thread = threading.Thread(target=filter_proxys, args=(total_proxies[start:end],))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
        
    filter_cost_seconds = int((datetime.now()-start_filter_time).total_seconds())
    print(f'\nsuccessfully filter {len(active_proxies)} active proxies using {time(filter_cost_seconds)}')

    if not active_proxies:
        print("No active proxies found. Exiting.")
        sys.exit(1)

    # 3. 开始并发刷播放量
    print(f'\nstart boosting {bv} at {datetime.now().strftime("%H:%M:%S")}')

    try:
        info = fetch_video_info(bv)
        bv = info['bvid']
        initial_view_count = info['stat']['view']
        current = initial_view_count
        print(f'Initial view count: {initial_view_count}')
    except Exception as e:
        print(f'Failed to get initial view count: {e}')
        sys.exit(1)

    while True:
        with stats_lock:
            reach_target = False
        start_time = datetime.now()
        
        # 将本轮可用代理推入队列
        proxy_queue = Queue()
        for i, proxy in enumerate(active_proxies):
            proxy_queue.put((proxy, i))
        
        # 启动多线程进行并发请求
        threads = []
        for _ in range(boost_thread_num):
            thread = threading.Thread(target=boost_view_worker, 
                                     args=(proxy_queue, info, bv, len(active_proxies), target, initial_view_count))
            thread.start()
            threads.append(thread)
        
        for thread in threads:
            thread.join()
        
        with stats_lock:
            target_reached = reach_target
        
        if not target_reached:
            proxy_queue.join()
            try:
                info = fetch_video_info(bv)
                with stats_lock:
                    current = info['stat']['view']
                    if current >= target:
                        reach_target = True
                        target_reached = True
            except:
                pass
        
        if target_reached:
            break
            
        remain_seconds = int(round_time-(datetime.now()-start_time).total_seconds())
        if remain_seconds > 0:
            for second in reversed(range(remain_seconds)):
                with stats_lock:
                    hits = successful_hits
                    view_increase = current - initial_view_count
                print(f'{pbar(current, target, hits, view_increase)} next round: {time(second)}          ', end='')
                sleep(1)

    # ================= 输出统计数据 =================
    success_rate = (successful_hits / len(active_proxies)) * 100 if active_proxies else 0
    print(f'\nFinish at {datetime.now().strftime("%H:%M:%S")}')
    print(f'Statistics:')
    print(f'- Initial views: {initial_view_count}')
    print(f'- Final views: {current}')
    print(f'- Total increase: {current - initial_view_count}')
    print(f'- Successful hits: {successful_hits}')
    print(f'- Success rate: {success_rate:.2f}%\n')