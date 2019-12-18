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
import numpy as np


class Client41(MyClient):
    def __init__(self):
        super().__init__()

        #多进程
        self.is_any_updated = False
        # self.is_any_updated_lock = threading.RLock()
        self.market_data_updated = []
        # self.market_data_updated_lock = threading.Lock()
        self.start_event = threading.Event()


        #做市策略参数
        self.market_bid_offer = []
        self.market_ask_offer = []
        self.market_ops = []

        self.options_prices = []
        self.options_names = []

        # parity
        self.ubiq_price = []
        self.option_info = []
        self.option_order = {}

    def myInit(self,multi_thread=True):
        self.get_price_list()

        for i in range(len(self.instruments)):
            self.option_order[i] = []

        # Strategy Thread
        def stragey_run(func, args, sleep_intervel):
            print("-------- waitting --------")
            self.start_event.wait()
            print("-------- go --------")
            while True:
                func(*args)
                time.sleep(sleep_intervel)

        # if multi_thread:
        #     for each in [(self.market_maker_strategy, 2)]:
        #     # for each in [(self.put_call_parity, 2)]:
        #     # for each in [(self.put_call_parity, 2), (self.market_maker_strategy, 3)]:
        #         strategy_thread = threading.Thread(target=stragey_run,
        #                                            args=(each[0], (), each[1]))
        #         strategy_thread.setDaemon(True)
        #         strategy_thread.start()

        return True

    ##以下为做市部分
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

            try:
                self.send_cancel_order(ask_offer)
            except:
                pass

            self.market_data_updated[self.options_names.index(ask_offer.InstrumentID)] = False
        # with self.is_any_updated_lock:
        self.is_any_updated = False

    def market_maker_strategy(self):
        print("Market Maker")
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
                                             bid_price, 30)
            self.send_input_order(bid_order)
            self.market_bid_offer.append(bid_order)

            ask_order = om.place_limit_order(self.next_order_ref(),
                                             PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                             ask_price, 30)
            self.send_input_order(ask_order)
            self.market_ask_offer.append(ask_order)

            self.market_ops.append(yi_wu_option_pos)

            self.market_data_updated[yi_wu_option_pos] = False

        # with self.is_any_updated_lock:
            self.is_any_updated = False


    ##以下固定价差部分代码
    def fix_spread_strategy(self):
        index = self.ins2index["UBIQ"]
        benchmark_price = self.md_list[index][-1].LastPrice
        spread = 0.01  # todo modify
        bid_price = benchmark_price - spread
        bid_volume = 1
        ask_price = benchmark_price + spread
        ask_volume = 1

        om = self.ins2om["UBIQ"]
        bid_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open, bid_price, bid_volume)
        self.send_input_order(bid_order)
        self.market_bid_offer.append(bid_order)

        ask_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open, ask_price,
                                         ask_volume)
        self.send_input_order(ask_order)
        self.market_ask_offer.append(ask_order)

        while True:
            time.sleep(0.1)  # todo modify
            if bid_order.TradeVolume >= 1 or ask_order.TradeVolume >= 1:
                first_trade_price = bid_price if bid_order.TradeVolume >= 1 else ask_price
                benchmark_price = first_trade_price
                bid_price = benchmark_price - spread
                bid_volume = 2 if om.get_short_position_closeable() > 5 else 1  # self.shortSnapshot.Position
                ask_price = benchmark_price + spread
                ask_volume = 2 if om.get_long_position_closeable() > 5 else 1

                bid_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open, bid_price,
                                                 bid_volume)
                self.send_input_order(bid_order)
                self.market_bid_offer.append(bid_order)

                ask_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open, ask_price,
                                                 ask_volume)
                self.send_input_order(ask_order)
                self.market_ask_offer.append(ask_order)

                self.market_data_updated[72] = False
                self.is_any_updated = False

                # todo close?

    #parity
    # @WJ parity
    def update_option_order(self):
        order_list = self.option_order
        for i in range(len(self.instruments)):
            order_i = order_list[i]
            if len(order_i) >= 5:
                for j in range(len(order_i) - 5):
                    order_ins = order_i[j]
                    # print(order_ins)
                    if order_ins.VolumeTraded == order_ins.VolumeTotalOriginal:
                        if order_ins.Direction == "0":
                            if order_ins.OffsetFlag == "0":  # buy open
                                position_should_close = order_ins.VolumeTotalOriginal
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                time.sleep(0.01)
                                self.send_input_order(order)

                        if order_ins.Direction == "1":
                            if order_ins.OffsetFlag == "0":  # sell open
                                position_should_close = order_ins.VolumeTotalOriginal
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                self.send_input_order(order)
                                time.sleep(0.01)
                    if order_ins.VolumeTraded != order_ins.VolumeTotalOriginal:
                        if order_ins.Direction == "0":
                            if order_ins.OffsetFlag == "0":  # buy open
                                position_should_close = order_ins.VolumeTraded
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                self.send_input_order(order)
                                self.send_cancel_order(order_ins)
                                time.sleep(0.01)

                        elif order_ins.Direction == "1":
                            if order_ins.OffsetFlag == "0":  # sell open
                                position_should_close = order_ins.VolumeTraded
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                self.send_input_order(order)
                                time.sleep(0.01)
                                self.send_cancel_order(order_ins)
                                time.sleep(0.01)

                        if order_ins.Direction == "0":
                            if order_ins.OffsetFlag == "1":  # buy close
                                position_should_close = order_ins.VolumeTotalOriginal - order_ins.VolumeTraded
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                self.send_input_order(order)
                                time.sleep(0.01)
                                self.send_cancel_order(order_ins)
                                time.sleep(0.01)

                        if order_ins.Direction == "1":
                            if order_ins.OffsetFlag == "1":  # sell close
                                position_should_close = order_ins.VolumeTotalOriginal - order_ins.VolumeTraded
                                om = self.ins2om[self.instruments[i].InstrumentID]
                                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close,
                                                              position_should_close)
                                self.send_input_order(order)
                                time.sleep(0.01)
                                self.send_cancel_order(order_ins)
                                time.sleep(0.01)

                    # print(order_ins.InstrumentID, self.instruments[i])
                    # print(order_ins.LimitPrice,order_ins.VolumeTraded,order_ins.VolumeTotalOriginal)
                self.option_order[i] = order_i[-5:]


    def put_call_parity(self):
        tau = self.game_status.CurrGameCycleLeftTime
        tau = tau / 3600
        r = 0
        sigma = 0.025
        index = self.ins2index["UBIQ"]
        S_last = self.md_list[index][-1].LastPrice
        S_last_last = self.md_list[index][-2].LastPrice
        self.ubiq_price.append((S_last - S_last_last) / S_last_last)
        if len(self.ubiq_price) > 50:
            sigma = np.std(self.ubiq_price) * np.sqrt(7200)

        K = self.options_prices
        S_ask = self.md_list[index][-1].AskPrice1
        S_bid = self.md_list[index][-1].BidPrice1

        threshold = 0.02
        longcall = {}
        shortcall = {}
        longput = {}
        shortput = {}
        for i in range(len(K)):
            # print(i)
            strike = K[i]
            ins_call = self.instruments[i]
            index_call = self.ins2index[ins_call.InstrumentID]
            ins_put = self.instruments[i + 36]
            index_put = self.ins2index[ins_put.InstrumentID]

            # longcall['C' + str(strike)] = bs_call(S_bid, strike, tau, r, sigma)
            # shortcall['C' + str(strike)] = bs_call(S_ask, strike, tau, r, sigma)
            # longput['P' + str(strike)] = bs_put(S_ask, strike, tau, r, sigma)
            # shortput['P' + str(strike)] = bs_put(S_bid, strike, tau, r, sigma)
            # pcall = (longcall['C' + str(strike)] + shortcall['C' + str(strike)]) / 2
            # pput = (longput['P' + str(strike)] + shortput['P' + str(strike)]) / 2

            try:
                ask_call = self.md_list[index_call][-1].AskPrice1
                ask_put = self.md_list[index_put][-1].AskPrice1
                bid_call = self.md_list[index_call][-1].BidPrice1
                bid_put = self.md_list[index_put][-1].BidPrice1

                sign_ask = ask_call - ask_put + strike - S_ask
                sign_put = bid_call - bid_put + strike - S_bid
                # print(pcall, pput, ask_call, ask_put)
                # print('unparity', sign_ask, sign_put)
                self.update_option_order()
                if sign_ask > threshold and sign_put > threshold:
                    om = self.ins2om[ins_put.InstrumentID]
                    # a,b = om.get_live_orders()
                    # print('put',[i for i in a],[i for i in b])
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 max(bid_put + 0.001, 0.001), 10)
                    self.send_input_order(order)
                    self.option_order[i + 36].append(order)

                    om = self.ins2om[ins_call.InstrumentID]
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 max(ask_call - 0.001, 0.001), 10)
                    self.send_input_order(order)
                    self.option_order[i].append(order)

                elif sign_ask < -threshold and sign_put < -threshold:
                    om = self.ins2om[ins_call.InstrumentID]
                    # a,b = om.get_live_orders()
                    # print('call',[i for i in a],[i for i in b])
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 max(bid_call + 0.001, 0.001), 10)
                    self.send_input_order(order)
                    self.option_order[i].append(order)

                    om = self.ins2om[ins_put.InstrumentID]
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 max(ask_put - 0.001, 0.001), 10)
                    self.send_input_order(order)
                    self.option_order[i + 36].append(order)

                mma = len(self.option_order[0])
                for le in range(len(self.option_order)):
                    ma = len(self.option_order[le])
                    if mma < ma:
                        mma = ma
                if mma > 10:
                    om = self.ins2om[ins_call.InstrumentID]
                    # a,b = om.get_live_orders()
                    # print('parity',[i for i in a],[i for i in b])
                    long_pos_number = om.get_long_position_closeable()
                    short_pos_number = om.get_short_position_closeable()
                    if long_pos_number > 0:
                        order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close,
                                                      long_pos_number)
                        self.send_input_order(order)
                        self.option_order[i].append(order)

                    elif short_pos_number > 0:
                        order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                                      short_pos_number)
                        self.send_input_order(order)
                        self.option_order[i].append(order)

                    om = self.ins2om[ins_put.InstrumentID]
                    long_pos_number = om.get_long_position_closeable()
                    short_pos_number = om.get_short_position_closeable()
                    if long_pos_number > 0:
                        order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close,
                                                      long_pos_number)
                        self.send_input_order(order)
                        self.option_order[i + 36].append(order)

                    elif short_pos_number > 0:
                        order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close,
                                                      short_pos_number)
                        self.send_input_order(order)
                        self.option_order[i + 36].append(order)

                    bids, asks = om.get_untraded_orders()
                    for order in bids:
                        self.send_cancel_order(order)
                        time.sleep(0.01)
                    for order in asks:
                        self.send_cancel_order(order)
                        time.sleep(0.01)

                self.market_data_updated[i] = False  # reset flag
                self.market_data_updated[36 + i] = False  # reset flag

                self.market_data_updated[72] = False  # reset flag
                self.options_info.append([sigma, sign_ask, sign_put])
                # print([sigma, sign_ask, sign_put])
            except:
                continue
        self.is_any_updated = False  # reset flag

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

    if client.Init() and client.myInit(False):
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
                client.market_maker_strategy()##做市策略大概是因为下单太快被杀
                time.sleep(3)
                # client.put_call_parity()
                # time.sleep(1)
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