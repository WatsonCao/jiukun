#!/usr/bin/env python
# encoding: utf-8
'''
@author: WatsonCao
@contact: chj5chj5@163.com 
@software: Pycharm
@file: Client.py
@time: 2019/12/15 16:29
@desc:
'''
from test.MyClient import *
import logging


class Client41(MyClient):
    def __init__(self):
        super().__init__()

        ##多进程
        self.is_any_updated = False
        self.is_any_updated_lock = threading.RLock()
        self.market_data_updated = []
        self.market_data_updated_lock = threading.Lock()

        self.start_event = threading.Event()


        #做市策略参数
        self.market_bid_offer = []
        self.market_ask_offer = []
        self.market_ops = []

        self.options_prices = []
        self.options_names = []


    def myInit(self):
        self.get_price_list()

        # Strategy Thread
        def stragey_run(func, args, sleep_intervel):
            print("-------- waitting --------")
            self.start_event.wait()
            print("-------- go --------")
            while True:
                func(*args)
                time.sleep(sleep_intervel)

        for each in [(self.run_strategy, 1), (self.market_maker_strategy, 1)]:
            strategy_thread = threading.Thread(target=stragey_run,
                                               args=(each[0], (), each[1]))
            strategy_thread.setDaemon(True)
            strategy_thread.start()

        return True

    ##以下为做市部分代码
    def get_price_list(self):
        for op in self.instruments[:36]:
            self.options_prices.append(op.StrikePrice)

        for op in self.instruments[:-1]:
            self.options_names.append(op.InstrumentID)

    def close_market(self):
        while len(self.market_bid_offer) != 0:
            bid_offer = self.market_bid_offer.pop()
            vol_traded = bid_offer.VolumeTraded
            om = self.ins2om[bid_offer.InstrumentID]
            if vol_traded > 0:
                order = om.place_market_order(self.next_order_ref(),
                                              PHX_FTDC_D_Sell,
                                              PHX_FTDC_OF_Close, vol_traded)
                self.send_input_order(order)
            try:
                self.send_cancel_order(bid_offer)
            except:
                pass
            self.market_data_updated[self.options_names.index(bid_offer.InstrumentID)] = False

        while len(self.market_ask_offer) != 0:
            ask_offer = self.market_ask_offer.pop()
            vol_traded = ask_offer.VolumeTraded
            om = self.ins2om[ask_offer.InstrumentID]
            if vol_traded > 0:
                order = om.place_market_order(self.next_order_ref(),
                                              PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                              vol_traded)
                self.send_input_order(order)
            self.send_cancel_order(ask_offer)
            self.market_data_updated[self.options_names.index(ask_offer.InstrumentID)] = False
        # with self.is_any_updated_lock:
        self.is_any_updated = False

    def market_maker_strategy(self):
        # logging.info("market_maker_strategy")
        self.close_market()
        index = self.ins2index["UBIQ"]
        ubi_price = self.md_list[index][-1].LastPrice

        down_price = ubi_price * 0.90
        up_price = ubi_price * 1.10
        l_pos = r_pos = -1
        for pos in range(len(self.options_prices)):
            if self.options_prices[pos] >= down_price and l_pos == -1:
                l_pos = pos
            if self.options_prices[pos] <= up_price:
                r_pos = pos

        for yi_wu_option_pos in list(range(l_pos, r_pos + 1)) + list(
                range(l_pos + 36, r_pos + 1 + 36)):

            ins = self.instruments[yi_wu_option_pos]
            om = self.ins2om[ins.InstrumentID]

            try:
                current_op_price = self.md_list[yi_wu_option_pos][-1].LastPrice

                biggest_spread = 0
                if current_op_price < 0.1:
                    biggest_spread = 0.005
                elif current_op_price >= 0.1 and current_op_price < 0.2:
                    biggest_spread = 0.01
                elif current_op_price >= 0.2 and current_op_price < 0.5:
                    biggest_spread = 0.025
                elif current_op_price >= 0.5 and current_op_price <= 1.0:
                    biggest_spread = 0.05
                elif current_op_price > 1.0:
                    biggest_spread = 0.08

                bid_price = current_op_price - (
                        biggest_spread * 1000 // 2) / 1000.0
                ask_price = current_op_price + (
                        biggest_spread * 1000 // 2) / 1000.0
            except:
                current_op_price = 0.49
                bid_price = current_op_price - (0.025 * 1000 // 3) / 1000.0
                ask_price = current_op_price + (0.025 * 1000 // 3) / 1000.0

            if ask_price <= 0.001 or bid_price <= 0.001:
                continue

            bid_order = om.place_limit_order(self.next_order_ref(),
                                             PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                             bid_price, 20)
            self.send_input_order(bid_order)
            self.market_bid_offer.append(bid_order)

            ask_order = om.place_limit_order(self.next_order_ref(),
                                             PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                             ask_price, 20)
            self.send_input_order(ask_order)
            self.market_ask_offer.append(ask_order)

            self.market_ops.append(yi_wu_option_pos)

            self.market_data_updated[yi_wu_option_pos] = False

        # with self.is_any_updated_lock:
        self.is_any_updated = False


if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option("-i", "--ip", dest="ip", help="server ip")
    parser.add_option("-p", "--port", dest="port", help="server ip")
    parser.add_option("-u", "--user_id", dest="user_id", help="user id")
    parser.add_option("-a", "--password", dest="password", help="password")
    (options, args) = parser.parse_args()
    server_ip = '106.120.131.90'
    order_port = 9000
    user_id = 41
    password = '8V2pmCbX'

    if options.ip:
        server_ip = options.ip
    if options.port:
        order_port = int(options.port)
    if options.user_id:
        user_id = int(options.user_id)
    if options.password:
        password = options.password

    client = Client41()
    client.serverHost = server_ip
    client.serverOrderPort = order_port
    client.serverRtnPort = order_port + 1
    client.serverQryPort = order_port + 2
    client.serverMDPort = order_port + 3
    client.m_UserID = user_id
    client.m_Passwd = password

    if client.Init() and client.myInit():
        print("init success")
        resetted = True
        while True:
            if client.game_status is None or (not client.m_pUserApi.all_connected):
                print("server not started")
                time.sleep(1)
            elif client.game_status.GameStatus == 0:
                print("game not started, waitting for start")
                time.sleep(1)
            elif client.game_status.GameStatus == 1:
                resetted = False
                client.start_event.set()
                time.sleep(0.5)
            elif client.game_status.GameStatus == 2:
                print("game settling")
                time.sleep(1)
            elif client.game_status.GameStatus == 3:
                print("game settled, waiting for next round")
                if not resetted:
                    client.reset()
                    resetted = True
                    print("client resetted")
                time.sleep(1)
            elif client.game_status.GameStatus == 4:
                print("game finished")
                break
    else:
        print("init failed")