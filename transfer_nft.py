#!/usr/bin/python3
import yaml
import time

import tenacity
from tenacity import stop_after_attempt, wait_fixed, retry_if_exception_type, RetryCallState
import logging
import requests
from requests.exceptions import RequestException
import functools
from decimal import Decimal
from typing import List, Dict
import base64

import contract
import logger
import utils
from utils import plat
import res
from res import Resoure, Farming, Token
from datetime import datetime, timedelta
from settings import cfg
from settings import load_user_param, user_param
import os
from logger import log


class FarmerException(Exception):
    pass


class CookieExpireException(FarmerException):
    pass


# 调用智能合约出错，此时应停止并检查日志，不宜反复重试
class TransactException(FarmerException):
    # 有的智能合约错误可以重试,-1为无限重试
    def __init__(self, msg, retry=True, max_retry_times: int = -1):
        super().__init__(msg)
        self.retry = retry
        self.max_retry_times = max_retry_times


# 遇到不可恢复的错误 ,终止程序
class StopException(FarmerException):
    pass


class Status:
    Continue = 1
    Stop = 2


class Farmer:
    # wax rpc
    # url_rpc = "https://api.wax.alohaeos.com/v1/chain/"
    # url_rpc = "https://wax.dapplica.io/v1/chain/"
    # url_table_row = url_rpc + "get_table_rows"
    # 资产API
    # url_assets = "https://wax.api.atomicassets.io/atomicassets/v1/assets"
    # url_assets = "https://atomic.wax.eosrio.io/atomicassets/v1/assets"
    waxjs: str = None
    myjs: str = None
    chrome_data_dir = os.path.abspath(cfg.chrome_data_dir)

    def __init__(self):
        self.url_rpc: str = None
        self.url_table_row: str = None
        self.url_assets: str = None

        self.wax_account: str = None
        self.login_name: str = None
        self.password: str = None
        self.proxy: str = None
        self.http: requests.Session = None
        self.cookies: List[dict] = None
        self.log: logging.LoggerAdapter = log
        # 下一次可以操作东西的时间
        self.next_operate_time: datetime = datetime.max
        # 下一次扫描时间
        self.next_scan_time: datetime = datetime.min
        # 本轮扫描中暂不可操作的东西
        self.not_operational: List[Farming] = []
        # 智能合约连续出错次数
        self.count_error_transact = 0
        # 本轮扫描中作物操作成功个数
        self.count_success_claim = 0
        # 本轮扫描中作物操作失败个数
        self.count_error_claim = 0
        # 本轮开始时的资源数量
        self.resoure: Resoure = None
        self.token: Token = None

    def close(self):
        self.log.info("稍等，程序正在退出")


    def init(self):
        self.log.extra["tag"] = self.wax_account
        self.url_rpc = user_param.rpc_domain + '/v1/chain/'
        self.url_table_row = user_param.rpc_domain + '/v1/chain/get_table_rows'
        self.url_assets = user_param.assets_domain + '/atomicassets/v1/assets'
        self.http = requests.Session()
        self.http.trust_env = False
        self.http.request = functools.partial(self.http.request, timeout=30)
        if self.proxy:
            self.http.proxies = {
                "http": "http://{0}".format(self.proxy),
                "https": "http://{0}".format(self.proxy),
            }
        http_retry_wrapper = tenacity.retry(wait=wait_fixed(cfg.req_interval), stop=stop_after_attempt(5),
                                            retry=retry_if_exception_type(RequestException),
                                            before_sleep=self.log_retry, reraise=True)
        self.http.get = http_retry_wrapper(self.http.get)
        self.http.post = http_retry_wrapper(self.http.post)



    def start(self):
        self.log.info("wax节点: {0}".format(user_param.rpc_domain))
        self.log.info("原子市场节点: {0}".format(user_param.assets_domain))
        time.sleep(cfg.req_interval)


    def log_retry(self, state: RetryCallState):
        exp = state.outcome.exception()
        if isinstance(exp, RequestException):
            self.log.info("网络错误: {0}".format(exp))
            self.log.info("正在重试: [{0}]".format(state.attempt_number))

    # template_id: [大麦 318606] [玉米 318607]
    def scan_assets(self):
        payload = {
            "limit": 1000,
            "collection_name": "farmersworld",
            "owner": self.wax_account,
            # "template_id": template_id,
        }
        resp = self.http.get(self.url_assets, params=payload)
        self.log.debug("get_chests:{0}".format(resp.text))
        resp = resp.json()
        return resp

    # 转账
    def scan_transfer(self):
        self.log.info("开始扫描NFT")
        assets = self.scan_assets()
        barley_list = []
        barleyseed_list = []
        corn_list = []
        cornseed_list = []
        fcoin_list = []
        milk_list = []
        log_text = ''
        for item in assets['data']:
            if item['template']['template_id'] == "318606":
                # 大麦
                # barley_list.append({"asset_id": item.asset_id, "name": item.name, "cn_name": "大麦"})
                barley_list.append(item['asset_id'])
            if item['template']['template_id'] == "318607":
                # 玉米
                # corn_list.append({"asset_id": item.asset_id, "name": item.name, "cn_name": "玉米"})
                corn_list.append(item['asset_id'])
            if item['template']['template_id'] == "260676":
                # 农夫币
                # fcoin_list.append({"asset_id": item.asset_id, "name": item.name, "cn_name": "农夫币"})
                fcoin_list.append(item['asset_id'])
            if item['template']['template_id'] == "298593":
                # 牛奶
                # milk_list.append({"asset_id": item.asset_id, "name": item.name, "cn_name": "牛奶"})
                milk_list.append(item['asset_id'])
            if item['template']['template_id'] == "298595":
                # 大麦种子
                barleyseed_list.append(item['asset_id'])
            if item['template']['template_id'] == "298596":
                # 玉米种子
                cornseed_list.append(item['asset_id'])
        self.log.info(
            f"查询完成【大麦{len(barley_list)}】【玉米{len(corn_list)}】【大麦种子{len(barleyseed_list)}】【玉米种子{len(cornseed_list)}】")
        self.log.info(f"【农夫币{len(fcoin_list)}】【牛奶{len(milk_list)}】")
        self.log.info("开始转移NFT")
        for accountItem in transfer_nft_config.transfer_list:
            transfer_asset_ids = []
            if 0 < accountItem['transfer_barley'] <= len(barley_list):
                transfer_asset_ids.extend(barley_list[0:accountItem['transfer_barley']])
                barley_list = barley_list[accountItem['transfer_barley']:]
                self.log.info(f"【大麦】转移{accountItem['transfer_barley']}个")
            elif 0 < accountItem['transfer_barley'] and accountItem['transfer_barley'] > len(barley_list):
                transfer_asset_ids.extend(barley_list)
                self.log.info(f"【大麦】需要转移{accountItem['transfer_barley']}个，大麦数量不足，剩余{len(barley_list)}个将全部转移")
                barley_list = []

            if 0 < accountItem['transfer_corn'] <= len(corn_list):
                transfer_asset_ids.extend(corn_list[0:accountItem['transfer_corn']])
                corn_list = corn_list[accountItem['transfer_corn']:]
                self.log.info(f"【玉米】转移{accountItem['transfer_corn']}个")
            elif 0 < accountItem['transfer_corn'] and accountItem['transfer_corn'] > len(corn_list):
                transfer_asset_ids.extend(corn_list)
                self.log.info(f"【玉米】需要转移{accountItem['transfer_corn']}个，玉米数量不足，剩余{len(corn_list)}个将全部转移")
                corn_list = []

            if 0 < accountItem['transfer_fcoin'] <= len(fcoin_list):
                transfer_asset_ids.extend(fcoin_list[0:accountItem['transfer_fcoin']])
                fcoin_list = fcoin_list[accountItem['transfer_fcoin']:]
                self.log.info(f"【农夫币】转移{accountItem['transfer_fcoin']}个")
            elif 0 < accountItem['transfer_fcoin'] and accountItem['transfer_fcoin'] > len(fcoin_list):
                transfer_asset_ids.extend(fcoin_list)
                self.log.info(f"【农夫币】需要转移{accountItem['transfer_fcoin']}个，农夫币数量不足，剩余{len(fcoin_list)}个将全部转移")
                fcoin_list = []

            if 0 < accountItem['transfer_milk'] <= len(milk_list):
                transfer_asset_ids.extend(milk_list[0:accountItem['transfer_milk']])
                milk_list = milk_list[accountItem['transfer_milk']:]
                self.log.info(f"【牛奶】转移{accountItem['transfer_milk']}个")
            elif 0 < accountItem['transfer_milk'] and accountItem['transfer_milk'] > len(milk_list):
                transfer_asset_ids.extend(milk_list)
                self.log.info(f"【牛奶】需要转移{accountItem['transfer_milk']}个，牛奶数量不足，剩余{len(milk_list)}个将全部转移")
                milk_list = []

            if 0 < accountItem['transfer_barleyseed'] <= len(barleyseed_list):
                transfer_asset_ids.extend(barleyseed_list[0:accountItem['transfer_barleyseed']])
                barleyseed_list = barleyseed_list[accountItem['transfer_barleyseed']:]
                self.log.info(f"【大麦种子】转移{accountItem['transfer_barleyseed']}个")
            elif 0 < accountItem['transfer_barleyseed'] and accountItem['transfer_barleyseed'] > len(barleyseed_list):
                transfer_asset_ids.extend(barleyseed_list)
                self.log.info(f"【大麦种子】需要转移{accountItem['transfer_barleyseed']}个，大麦种子数量不足，剩余{len(barleyseed_list)}个将全部转移")
                barleyseed_list = []

            if 0 < accountItem['transfer_cornseed'] <= len(cornseed_list):
                transfer_asset_ids.extend(cornseed_list[0:accountItem['transfer_cornseed']])
                cornseed_list = cornseed_list[accountItem['transfer_cornseed']:]
                self.log.info(f"【大麦种子】转移{accountItem['transfer_cornseed']}个")
            elif 0 < accountItem['transfer_cornseed'] and accountItem['transfer_cornseed'] > len(cornseed_list):
                transfer_asset_ids.extend(cornseed_list)
                self.log.info(f"【大麦种子】需要转移{accountItem['transfer_cornseed']}个，大麦种子数量不足，剩余{len(cornseed_list)}个将全部转移")
                cornseed_list = []

            if len(transfer_asset_ids) > 0:
                self.do_transfer(accountItem['reveive_account'], transfer_asset_ids)
                time.sleep(2)
            else:
                self.log.info("没有可转移的nft")

        return True

    # 转账
    def do_transfer(self, account, transfer_asset_ids):
        self.log.info("正在转移NFT给【{0}】".format(account))

        transaction = {
            "actions": [{
                "account": "atomicassets",
                "name": "transfer",
                "authorization": [{
                    "actor": self.wax_account,
                    "permission": "active",
                }],
                "data": {
                    "from": self.wax_account,
                    "to": account,
                    "asset_ids": transfer_asset_ids,
                    "memo": "OpenFarmer",
                },
            }],
        }
        self.wax_transact(transaction)
        self.log.info("转移完成【{0}】".format(account))

    def reset_before_scan(self):
        self.not_operational.clear()
        self.count_success_claim = 0
        self.count_error_claim = 0

    # 签署交易(只许成功，否则抛异常）
    def wax_transact(self, transaction: dict):
        self.log.info("begin transact: {0}".format(transaction))
        success, result = contract.push_transaction(transaction)
        if success:
            self.log.info("transact ok, transaction_id: [{0}]".format(result["transaction_id"]))
            self.log.debug("transact result: {0}".format(result))
            time.sleep(1)
            return result
        else:
            if "is greater than the maximum billable" in result:
                self.log.error("CPU资源不足，可能需要质押更多WAX，一般为误报，稍后重试 maximum")
            elif "estimated CPU time (0 us) is not less than the maximum billable CPU time for the transaction (0 us)" in result:
                self.log.error("CPU资源不足，可能需要质押更多WAX，一般为误报，稍后重试 estimated")
            else:
                self.log.error("transact error: {0}".format(result))
            raise TransactException(result)

    # 检查正在培养的作物， 返回值：是否继续运行程序
    def scan_all(self) -> int:
        status = Status.Continue
        try:
            self.reset_before_scan()
            self.scan_transfer()
            self.log.info("程序运行结束")

        except TransactException as e:
            # self.log.exception("智能合约调用出错")
            if not e.retry:
                return Status.Stop
            self.count_error_transact += 1
            self.log.error("合约调用异常【{0}】次".format(self.count_error_transact))
            if self.count_error_transact >= e.max_retry_times and e.max_retry_times != -1:
                self.log.error("合约连续调用异常")
                return Status.Stop
            self.next_scan_time = datetime.now() + cfg.min_scan_interval
        except CookieExpireException as e:
            self.log.exception(str(e))
            self.log.error("Cookie失效，请手动重启程序并重新登录")
            return Status.Stop
        except StopException as e:
            self.log.exception(str(e))
            self.log.error("不可恢复错误，请手动处理，然后重启程序并重新登录")
            return Status.Stop
        except FarmerException as e:
            self.log.exception(str(e))
            self.log.error("常规错误，稍后重试")
            self.next_scan_time = datetime.now() + cfg.min_scan_interval
        except Exception as e:
            self.log.exception(str(e))
            self.log.error("常规错误，稍后重试")
            self.next_scan_time = datetime.now() + cfg.min_scan_interval

        return status

    def run_forever(self):
        while True:
            status = self.scan_all()
            if status == Status.Stop:
                self.close()
                self.log.info("程序已停止，请检查日志后手动重启程序")
                return 1
            # 1800秒（半小时）跑一次
            # time.sleep(1800)
            return 0


class transfer_nft_config:
    transfer_list: list = []


def run(config_file: str, transfer_nft_yml: str):
    with open(config_file, "r", encoding="utf8") as file:
        user: dict = yaml.load(file, Loader=yaml.FullLoader)
        file.close()
    load_user_param(user)
    with open(transfer_nft_yml, "r", encoding="utf8") as file:
        transfer_dict: dict = yaml.load(file, Loader=yaml.FullLoader)
        transfer_nft_config.transfer_list = transfer_dict.get("list", [])
        file.close()

    logger.init_loger(user_param.wax_account)
    log.info("=======批量转nft功能=======")
    log.info("WAX账号: {0}".format(user_param.wax_account))
    farmer = Farmer()
    farmer.wax_account = user_param.wax_account
    if user_param.use_proxy:
        farmer.proxy = user_param.proxy
        log.info("use proxy: {0}".format(user_param.proxy))
    farmer.init()
    farmer.start()
    log.info("开始自动化")
    return farmer.run_forever()


def main():
    try:
        user_yml = "user.yml"
        transfer_nft_yml = "transfer_nft.yml"
        run(user_yml, transfer_nft_yml)
    except Exception:
        log.exception("start error")
    # input()


if __name__ == '__main__':
    main()
