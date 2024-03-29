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
import random
from sqlalchemy import create_engine
import pandas as pd
from test.black_scholes import *


class Client41(MyClient):
    def __init__(self):
        super().__init__()

        #获得初始的价格信息
        self.ini_mkdata = deque()

        #多进程
        self.is_any_updated_lock = threading.RLock()
        self.market_data_updated_lock = threading.RLock()
        self.start_event = threading.Event()

        #做市策略参数
        self.market_bid_offer = []
        self.market_ask_offer = []
        self.market_ops = []

        self.options_prices = []
        self.options_names = []

        self.market_buy_close_order=deque()
        self.market_sell_close_order=deque()

        # parity
        self.ubiq_price = []
        self.option_info = []
        self.option_order = {}

        # 可视化代码
        self.logger = logging.getLogger(__name__)
        self.handler = logging.FileHandler("log.txt")
        self.logger.setLevel(level=logging.INFO)
        self.handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.handler.setFormatter(formatter)
        self.logger.addHandler(self.handler)
        self.market_log=None

        self.curr_all = 0
        self.curr_complete = 0
        self.curr_trade = 0
        self.last_all = -1
        self.last_complete = -1
        self.last_trade = -1

        # SQL存储
        self.in_sql=False
        self.engine = create_engine(
            'mysql+pymysql://root:chj5chj5@localhost/jiukun?charset=utf8')

        # wj
        self.parity_long_pos = {}
        self.parity_short_pos = {}
        self.parity_long_cost = {}
        self.parity_short_cost = {}
        self.parity_order_list = []

        self.monoto_order_list = []
        self.replace_order_list = []
        self.order_count = 0
        self.ubiq_order_list = []
        self.ask_bid = ['LastPrice', 'LastVolume',
                        'AskPrice1', 'AskVolume1', 'AskPrice2', 'AskVolume2',
                        'AskPrice3', 'AskVolume3', 'AskPrice4', 'AskVolume4',
                        'AskPrice5', 'AskVolume5', 'BidPrice1', 'BidVolume1',
                        'BidPrice2', 'BidVolume2', 'BidPrice3', 'BidVolume3',
                        'BidPrice4', 'BidVolume4', 'BidPrice5', 'BidVolume5', ]
        self.window = 100
        self.ubiq_price = deque()
        self.implied_vol = deque()
        self.option_price = deque()
        self.greeks = deque()
        self.position_order_list = []

        # Lock
        self.send_lock=threading.Lock()

        # spread
        self.spread_bid_offer=[]
        self.spread_ask_offer=[]
        self.last_time=None


    def myInit(self,multi_thread=True):
        self.get_price_list()

        for i in range(len(self.instruments)):
            self.option_order[i] = []

        # Strategy Thread
        def stragey_run(func, args, sleep_intervel,ran=False):
            print("-------- waitting --------")
            self.start_event.wait()
            print("-------- go --------")
            while True:
                func(*args)
                if ran:
                    time.sleep(sleep_intervel+random.uniform(-0.3, 0.3))
                else:
                    time.sleep(sleep_intervel)

        if multi_thread:
            for each in [(self.market_maker_strategy,1,True),(self.visual_position,2),(self.put_call_parity,2),(self.spread_strategy,2)]:
            # for each in [(self.spread_strategy, 0.4),(self.visual_position,2)]:
            # for each in [(self.visual_position, 2), (self.monoto_adj, 1)]:
            # for each in [(self.put_call_parity, 2)]:
            # for each in [(self.put_call_parity, 2), (self.market_maker_strategy, 3)]:
                strategy_thread = threading.Thread(target=stragey_run,
                                                   args=(each[0], (), each[1]))
                strategy_thread.setDaemon(True)
                strategy_thread.start()

        self.close_all()
        self.limit_close()
        self.last_time=time.time()

        return True

    # 辅助下单
    def send_input_order(self, order: OrderInfo):
        field = CPhxFtdcQuickInputOrderField()
        field.OrderPriceType = order.OrderPriceType
        field.OffsetFlag = order.OffsetFlag
        field.HedgeFlag = PHX_FTDC_HF_Speculation
        field.InstrumentID = order.InstrumentID
        field.Direction = order.Direction
        field.VolumeTotalOriginal = order.VolumeTotalOriginal
        field.TimeCondition = PHX_FTDC_TC_GFD
        field.VolumeCondition = PHX_FTDC_VC_AV
        if order.OrderPriceType == PHX_FTDC_OPT_LimitPrice:
            field.LimitPrice = order.LimitPrice
        field.OrderLocalID = order.OrderLocalID
        with self.send_lock:
            ret = self.m_pUserApi.ReqQuickOrderInsert(field, self.next_request_id())
            time.sleep(0.005)
        # Noted by WatsonCao
        # print("QuickOrderInsert ", field, ret)

    def send_cancel_order(self, order: OrderInfo):
        field = CPhxFtdcOrderActionField()
        field.OrderSysID = order.OrderSysID
        field.InvestorID = self.m_UserID
        field.OrderLocalID = order.OrderLocalID
        with self.send_lock:
            ret = self.m_pUserApi.ReqOrderAction(field, self.next_request_id())
            time.sleep(0.005)
        # Noted by WatsonCao
        # print("ActionOrder data=%s, ret=%d" % (json.dumps(field.__dict__), ret))

    #以下为辅助可视化输出部分
    def obtain_on_untraded_volume(self,om):
        # 全部成交 PHX_FTDC_OST_AllTraded = '0'
        # 部分成交还在队列中 PHX_FTDC_OST_PartTradedQueueing = '1'
        # 部分成交不在队列中 PHX_FTDC_OST_PartTradedNotQueueing = '2'
        # 未成交还在队列中 PHX_FTDC_OST_NoTradeQueueing = '3'
        # 未成交不在队列中 PHX_FTDC_OST_NoTradeNotQueueing = '4'
        # 撤单 PHX_FTDC_OST_Canceled = '5'
        # 未知 PHX_FTDC_OST_Unknown = '6'
        # 错单 PHX_FTDC_OST_Error = '7'
        num=0
        for order in om.OrderRef2OrderInfo.values():
            if order.OrderStatus=="1" and order.OrderStatus=="3":
                num+=order.VolumeTotalOriginal-order.VolumeTraded
        return num

    def visual_position(self):
        # with self.is_any_updated_lock:
        print("Visual Position")

        str1 = "\nCall\tLong\tShort\tPrice\tUntraded\tPut\tLong\tShort\tPrice\tUntraded\n"
        try:
            for pos in range(0, 36):
                om = self.ins2om[self.options_names[pos]]
                str1 += self.options_names[pos] + "\t" + str(om.longSnapshot.Position) + "\t" + str(
                    om.shortSnapshot.Position) \
                        + "\t" + "{:.4f}".format(self.md_list[pos][-1].LastPrice) + "\t" + "{}".format(
                    om.get_live_order_num()) + "\t\t"
                om = self.ins2om[self.options_names[pos + 36]]
                str1 += self.options_names[pos + 36] + "\t" + str(om.longSnapshot.Position) + "\t" + str(
                    om.shortSnapshot.Position) + "\t" + "{:.4f}".format(self.md_list[pos + 36][-1].LastPrice) + "\t" \
                        + "{}".format(om.get_live_order_num()) + "\n"
            om = self.ins2om[self.options_names[72]]
            str1 += self.options_names[72] + "\t" + str(om.longSnapshot.Position) + "\t" + str(
                om.shortSnapshot.Position) + "\t" + "{:.4f}".format(self.md_list[72][-1].LastPrice) + "\t" \
                    + "{}".format(om.get_live_order_num()) + "\n"
        except Exception as e:
            print(e)

        if self.market_log != None:
            str1 += "PreBalance:{:.4f}\t\tCurrMargin:{:.4f}\t\tAvailable:{:.4f}\nFloatProfit:{:.4f}\t\tCloseProfit:{:.4f}\n".format(
                self.market_log["PreBalance"], self.market_log["CurrMargin"], self.market_log["Available"],
                self.market_log["FloatProfit"], self.market_log["CloseProfit"])
            str1 += "TotalMMCount:{:}\t\tMMCompleteCount:{:}\t\tTotalOptionTradeCount:{:}\n".format(
                self.market_log["TotalMarketMakingCount"], self.market_log["TotalMarketMakingCompleteCount"],
                self.market_log["TotalOptionTradeCount"])

            if self.curr_all != self.market_log["TotalMarketMakingCount"]:
                self.last_all = self.curr_all
                self.last_complete = self.curr_complete
                self.last_trade = self.curr_trade
                self.curr_all = self.market_log["TotalMarketMakingCount"]
                self.curr_complete = self.market_log["TotalMarketMakingCompleteCount"]
                self.curr_trade = self.market_log["TotalOptionTradeCount"]

            str1 += "DeltaTotal:{:}\t\tDeltaCount:{:}\t\tffDeltaTrade:{:}\n".format(
                self.curr_all - self.last_all, self.curr_complete - self.last_complete,
                self.curr_trade - self.last_trade)

            str1+= "Game Time Remained:{:}".format(self.game_status)+"\n"

        self.logger.info(str1)
        pass

    def OnRspQryTradingAccount(self, pTradingAccount: CPhxFtdcRspClientAccountField, ErrorID, nRequestID, bIsLast):
        tmp=pTradingAccount.__dict__
        print('OnRspQryTradingAccount, data=%s, ErrorID=%d, ErrMsg=%s, bIsLast=%d' % (json.dumps(tmp), ErrorID, get_server_error(ErrorID), bIsLast))
        self.market_log=tmp

    def OnRtnMarketData(self, pMarketData: CPhxFtdcDepthMarketDataField):

        if len(self.ins2index)>=73:
            while len(self.ini_mkdata)>0:
                tmp_pk=self.ini_mkdata.pop()
                index = self.ins2index[tmp_pk.InstrumentID]
                self.md_list[index].append(tmp_pk)
                self.market_data_updated[index] = True
                self.is_any_updated = True

            if pMarketData.InstrumentID in self.ins2index:
                # print('OnRtnMarketData, data=%s' % json.dumps(pMarketData.__dict__))
                if self.in_sql:
                    try:
                        df = pd.DataFrame({time.time(): pMarketData.__dict__}).T
                        df.to_sql('jiukun_1220', self.engine, index=True, if_exists='append')
                    except Exception as e:
                        pass
                index = self.ins2index[pMarketData.InstrumentID]
                self.md_list[index].append(pMarketData)
                self.market_data_updated[index] = True
                self.is_any_updated = True
        else:
            self.ini_mkdata.append(pMarketData)

        if pMarketData.InstrumentID == "UBIQ":
            self.ubiq_price.append([pMarketData.LastPrice,pMarketData.LastVolume,
                                    pMarketData.AskPrice1,pMarketData.AskVolume1,
                                    pMarketData.AskPrice2, pMarketData.AskVolume2,
                                    pMarketData.AskPrice3, pMarketData.AskVolume3,
                                    pMarketData.AskPrice4, pMarketData.AskVolume4,
                                    pMarketData.AskPrice5, pMarketData.AskVolume5,
                                    pMarketData.BidPrice1, pMarketData.BidVolume1,
                                    pMarketData.BidPrice2, pMarketData.BidVolume2,
                                    pMarketData.BidPrice3, pMarketData.BidVolume3,
                                    pMarketData.BidPrice4, pMarketData.BidVolume4,
                                    pMarketData.BidPrice5, pMarketData.BidVolume5])
            if len(self.ubiq_price) > self.window + 1:
                self.ubiq_price.popleft()

    ##以下为做市部分
    def get_price_list(self):
        for op in self.instruments[:36]:
            self.options_prices.append(op.StrikePrice)

        for op in self.instruments:
            self.options_names.append(op.InstrumentID)

    def close_all(self):
        # for pos in [72]:
        for pos in range(len(self.instruments)):
            ins = self.instruments[pos]
            om = self.ins2om[ins.InstrumentID]

            bids, asks = om.get_live_orders()
            for order in bids:
                self.send_cancel_order(order)
                # time.sleep(0.01)
            for order in asks:
                self.send_cancel_order(order)
                # time.sleep(0.01)

            long_pos_number = om.get_long_position_closeable()
            short_pos_number=om.get_short_position_closeable()

            self_trade=min(long_pos_number,short_pos_number)

            if long_pos_number>short_pos_number:
                long_pos_number-=self_trade
            else:
                short_pos_number-=self_trade

            while self_trade>0:
                if self_trade>=100:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close, 100)
                    self.send_input_order(order)

                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close, 100)
                    self.send_input_order(order)
                    self_trade-=100
                else:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close, self_trade)
                    self.send_input_order(order)

                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close, self_trade)

                    self.send_input_order(order)
                    self_trade =0



            while long_pos_number > 0:
                if long_pos_number >= 100:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close, 100)
                    self.send_input_order(order)
                else:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close,
                                                  long_pos_number)
                    self.send_input_order(order)
                self.market_data_updated[pos] = False
                long_pos_number-=100
                # time.sleep(0.01)

            while short_pos_number > 0:
                if short_pos_number >= 100:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close, 100)
                    self.send_input_order(order)
                else:
                    order = om.place_market_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close,
                                                  short_pos_number)
                    self.send_input_order(order)
                self.market_data_updated[pos] = False
                short_pos_number-=100
                # time.sleep(0.01)

            self.market_data_updated[pos] = False

        self.is_any_updated = False

        print("Try to close all")
        pass

    def close_market(self):
        # sell_close_pos=[0]*72
        # buy_close_pos=[0]*72
        #
        # while len(self.market_sell_close_order) > 0:
        #     tmp = self.market_sell_close_order.popleft()
        #     vol_remain = tmp.VolumeTotalOriginal - tmp.VolumeTraded
        #     if vol_remain > 0:
        #         sell_close_pos[self.ins2index[tmp.InstrumentID]] += vol_remain
        #
        # while len(self.market_buy_close_order) > 0:
        #     tmp = self.market_buy_close_order.popleft()
        #     vol_remain = tmp.VolumeTotalOriginal - tmp.VolumeTraded
        #     if vol_remain > 0:
        #         buy_close_pos[self.ins2index[tmp.InstrumentID]] += vol_remain
        #
        # # print(buy_close_pos)
        # # print(sell_close_pos)
        #
        # for pos in range(72):
        #     om = self.ins2om[self.options_names[pos]]
        #     while sell_close_pos[pos] > 0:
        #         if sell_close_pos[pos] >= 100:
        #             order = om.place_market_order(self.next_order_ref(),
        #                                           PHX_FTDC_D_Sell,
        #                                           PHX_FTDC_OF_Close, 100)
        #             self.send_input_order(order)
        #         else:
        #             order = om.place_market_order(self.next_order_ref(),
        #                                           PHX_FTDC_D_Sell,
        #                                           PHX_FTDC_OF_Close,
        #                                           sell_close_pos[pos])
        #             self.send_input_order(order)
        #         self.market_sell_close_order.append(order)
        #         self.market_data_updated[pos] = False
        #         sell_close_pos[pos] -= 100
        #         # time.sleep(0.01)
        #
        # for pos in range(72):
        #     om = self.ins2om[self.options_names[pos]]
        #     while buy_close_pos[pos] > 0:
        #         if buy_close_pos[pos] >= 100:
        #             order = om.place_market_order(self.next_order_ref(),
        #                                           PHX_FTDC_D_Buy,
        #                                           PHX_FTDC_OF_Close,
        #                                           100)
        #             self.send_input_order(order)
        #         else:
        #             order = om.place_market_order(self.next_order_ref(),
        #                                           PHX_FTDC_D_Buy,
        #                                           PHX_FTDC_OF_Close,
        #                                           buy_close_pos[pos])
        #             self.send_input_order(order)
        #         self.market_buy_close_order.append(order)
        #         self.market_data_updated[pos] = False
        #         buy_close_pos[pos] -= 100
                # time.sleep(0.01)

        while len(self.market_bid_offer) != 0:
            bid_offer = self.market_bid_offer.pop()
            vol_traded = bid_offer.VolumeTraded
            om = self.ins2om[bid_offer.InstrumentID]
            if vol_traded > 0:
                order = om.place_market_order(self.next_order_ref(),
                                              PHX_FTDC_D_Sell,
                                              PHX_FTDC_OF_Close,
                                              vol_traded )
                self.send_input_order(order)
                self.market_sell_close_order.append(order)
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
                                              vol_traded )
                self.send_input_order(order)
                self.market_buy_close_order.append(order)
            try:
                self.send_cancel_order(ask_offer)
            except:
                pass
            self.market_data_updated[self.options_names.index(ask_offer.InstrumentID)] = False

        self.is_any_updated = False

    def get_intrinsic_price(self,S,ins):
        K=ins.StrikePrice
        In_id=ins.InstrumentID
        if In_id[0]=="C":
            return max(S-K,0.001)
        elif In_id[0]=="P":
            return min(K-S,0.001)

    def market_maker_strategy(self):
        print("Market Maker")
        self.close_market()
        # K = self.options_prices * 2
        index = self.ins2index["UBIQ"]
        ubi_price = self.md_list[index][-1].LastPrice

        price = self.md_list[index][-1]
        S_ask = price.AskPrice1
        S_bid = price.BidPrice1
        S_ave = (S_ask + S_bid) / 2
        tau = self.game_status.CurrGameCycleLeftTime
        r = 0
        price = np.array(self.ubiq_price)[:, 0]
        returns = np.diff(price) / price[:-1]
        sigma = np.std(returns)
        returns = 2 * returns
        sigma = sigma * np.sqrt(2)
        if len(returns) < 10:
            sigma = 0.0001
        K = self.options_prices*2
        op_price = [0.001] * 72
        for i in range(18):
            op_price[i] = bs_call(S_ave, K[i], tau, r, sigma)
        for i in range(36, 54):
            op_price[i] = bs_put(S_ave, K[i], tau, r, sigma)


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

            current_op_price =self.get_intrinsic_price(ubi_price,ins)

            l=[current_op_price]

            last_op_price = self.md_list[yi_wu_option_pos][-1].LastPrice
            # if abs(last_op_price-current_op_price)<1:
            l.append(last_op_price)
            if last_op_price<=0.001:
                continue

            bid_ask_price = (self.md_list[yi_wu_option_pos][-1].AskPrice1 + self.md_list[yi_wu_option_pos][
                -1].BidPrice1) / 2

            # if abs(bid_ask_price-current_op_price)<1:
            if bid_ask_price>0.001:
                l.append(bid_ask_price)


            bs_price=op_price[yi_wu_option_pos]

            # if abs(bs_price-current_op_price)<0.3:

            if bs_price>0.001:
                l.append(bs_price)

            for current_op_price in l:
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
                        biggest_spread * 1000 *3// 7) / 1000.0
                ask_price = current_op_price + (
                        biggest_spread * 1000 *3 // 7) / 1000.0

                if ask_price <= 0.001 or bid_price <= 0.001:
                    continue

                bid_order = om.place_limit_order(self.next_order_ref(),
                                                 PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 bid_price, 40)
                self.send_input_order(bid_order)
                self.market_bid_offer.append(bid_order)

                ask_order = om.place_limit_order(self.next_order_ref(),
                                                 PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 ask_price, 40)
                self.send_input_order(ask_order)
                self.market_ask_offer.append(ask_order)

                self.market_ops.append(yi_wu_option_pos)

                self.market_data_updated[yi_wu_option_pos] = False

            self.is_any_updated = False


    ## Spread_market

    def limit_close(self):
        print("Self traded")
        for pos in range(len(self.instruments)-1):
            ins = self.instruments[pos]
            om = self.ins2om[ins.InstrumentID]

            long_pos_number = om.get_long_position_closeable()
            short_pos_number=om.get_short_position_closeable()

            index = self.ins2index["UBIQ"]
            ubi_price = self.md_list[index][-1].LastPrice

            current_op_price = self.get_intrinsic_price(ubi_price, ins)

            # bid_ask_price = (self.md_list[pos][-1].AskPrice1 + self.md_list[pos][
            #     -1].BidPrice1) / 2

            bid_ask_price=current_op_price

            self_trade=min(long_pos_number,short_pos_number)

            if long_pos_number>short_pos_number:
                long_pos_number-=self_trade
            else:
                short_pos_number-=self_trade

            while self_trade>0:
                if self_trade>100:
                    order = om.place_limit_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close, bid_ask_price,100)
                    self.send_input_order(order)

                    order = om.place_limit_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close, bid_ask_price,100)
                    self.send_input_order(order)
                    self_trade-=100
                    self.market_data_updated[pos] = False
                else:
                    order = om.place_limit_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Sell,
                                                  PHX_FTDC_OF_Close, bid_ask_price,int(self_trade))
                    self.send_input_order(order)

                    order = om.place_limit_order(self.next_order_ref(),
                                                  PHX_FTDC_D_Buy,
                                                  PHX_FTDC_OF_Close,bid_ask_price ,int(self_trade))

                    self.send_input_order(order)
                    self_trade =0
                    self.market_data_updated[pos] = False
            self.market_data_updated[pos] = False
        self.is_any_updated = False

    def spread_strategy(self):
        print("Spread")
        if time.time()-self.last_time>=15:
            self.last_time = time.time()
            self.limit_close()
        index = self.ins2index["UBIQ"]
        ubi_price = self.md_list[index][-1].LastPrice

        down_price = ubi_price * 0.91
        up_price = ubi_price * 1.09
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

            ask1_price = self.md_list[yi_wu_option_pos][-1].AskPrice1-0.001
            ask1_volume= self.md_list[yi_wu_option_pos][-1].AskVolume1//2
            if ask1_volume>50:
                ask1_volume=50

            bid1_price=self.md_list[yi_wu_option_pos][-1].BidPrice1+0.001
            bid1_volume = self.md_list[yi_wu_option_pos][-1].BidVolume1//2
            if bid1_volume>50:
                bid1_volume=50

            ask1_volume=bid1_volume=min(ask1_volume,bid1_volume)

            if ask1_price-bid1_price>0.0002 and ask1_volume>=1 and bid1_volume>=1:
                bid_order = om.place_limit_order(self.next_order_ref(),
                                                 PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 bid1_price, bid1_volume)

                self.send_input_order(bid_order)
                self.spread_bid_offer.append([time.time(),bid_order])

                ask_order = om.place_limit_order(self.next_order_ref(),
                                                 PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 ask1_price, ask1_volume)
                self.send_input_order(ask_order)
                self.spread_ask_offer.append([time.time(),ask_order])

                self.market_data_updated[yi_wu_option_pos] = False

            self.is_any_updated = False

        # 撤单
        new_order = []
        for pos_time, order in self.spread_bid_offer+self.spread_ask_offer:
            ins = order.InstrumentID
            stop_time = 3
            if time.time() - pos_time > stop_time:
                if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                    self.send_cancel_order(order)
                    # print('order calceled')
                elif order.OrderStatus == PHX_FTDC_OST_Error or order.OrderStatus == PHX_FTDC_OST_Canceled or order.OrderStatus == PHX_FTDC_OST_AllTraded:
                    # new_order.append([pos_time,order])
                    # print('Order waiting...')
                    # print(order.OrderStatus)
                    continue
            else:
                new_order.append([pos_time, order])
            om = self.ins2om[ins]

            index = self.ins2index[ins]
            if time.time() - pos_time < stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                cur_price = self.get_bid_ask(index).values[0]
                benefit = 0.05
                if order.Direction == "0":
                    if order.OffsetFlag == "0":  # buy open

                        if order.LimitPrice < cur_price[12] - benefit:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                PHX_FTDC_OF_Close,
                                                                min(cur_price[13], order.VolumeTraded))
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)
                    elif order.OffsetFlag == "1":  # buy close
                        if order.LimitPrice > cur_price[2] + benefit:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                PHX_FTDC_OF_Close,
                                                                min(cur_price[3],
                                                                    order.VolumeTotalOriginal - order.VolumeTraded))
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)
                elif order.Direction == "1":
                    if order.OffsetFlag == "0":  # sell open
                        if order.LimitPrice > cur_price[2] + benefit:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                PHX_FTDC_OF_Close,
                                                                min(cur_price[3], order.VolumeTraded))
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)
                    elif order.OffsetFlag == "1":  # sell close
                        if order.LimitPrice < cur_price[12] + benefit:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                PHX_FTDC_OF_Close,
                                                                min([13],
                                                                    order.VolumeTotalOriginal - order.VolumeTraded))
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)
            elif time.time() - pos_time >= stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                if order.Direction == "0":
                    if order.OffsetFlag == "0":  # buy open
                        position_should_close = order.VolumeTraded
                        if position_should_close != 0:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                PHX_FTDC_OF_Close,
                                                                position_should_close)
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)

                    elif order.OffsetFlag == "1":  # sell close
                        position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                        if position_should_close != 0:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                PHX_FTDC_OF_Close,
                                                                position_should_close)
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)

                elif order.Direction == "1":
                    if order.OffsetFlag == "0":  # sell open
                        position_should_close = order.VolumeTraded
                        if position_should_close != 0:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                PHX_FTDC_OF_Close,
                                                                position_should_close)
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)

                    elif order.OffsetFlag == "1":  # buy close
                        position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                        if position_should_close != 0:
                            om = self.ins2om[ins]
                            order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                PHX_FTDC_OF_Close,
                                                                position_should_close)
                            self.send_input_order(order_close)
                            new_order.append([time.time(), order_close])
                            # time.sleep(0.01)

                self.market_data_updated[self.ins2index[ins]] = False  # reset flag
        self.is_any_updated = False  # reset flag


    # #WJ
    def get_bid_ask(self, Index, shift = 1):
        ins = self.instruments[Index]
        index = self.ins2index[ins.InstrumentID]
        price = self.md_list[index][-shift].__dict__
        df = pd.DataFrame.from_dict(price, orient='index').T
        return df.loc[:,self.ask_bid]

    def put_call_parity(self):
        if time.time() - self.last_time >= 15:
            self.last_time = time.time()
            self.limit_close()
        strategy = [2,3]
        for st in strategy:
            # strike_parity
            if st == 2:
                index = self.ins2index["UBIQ"]
                price = self.md_list[index][-1]
                S_ask = price.AskPrice1
                S_bid = price.BidPrice1
                S_ave = (S_ask + S_bid) / 2
                K = self.options_prices*2

                down_price = S_ave * 0.90
                up_price = S_ave * 1.10
                down_strike = -1
                up_strike = -1
                for i in range(1, 36):
                    if K[i] >= down_price and K[i - 1] < down_price:
                        down_strike = i
                    if K[i] >= up_price and K[i - 1] < up_price:
                        up_strike = i
                for i in range(down_strike, up_strike):
                    strike = K[i]
                    ins_call = self.instruments[i]
                    index_call = self.ins2index[ins_call.InstrumentID]
                    om_call = self.ins2om[ins_call.InstrumentID]
                    ins_put = self.instruments[i + 36]
                    index_put = self.ins2index[ins_put.InstrumentID]
                    om_put = self.ins2om[ins_put.InstrumentID]

                    ask_call = self.md_list[index_call][-1].AskPrice1
                    ask_put = self.md_list[index_put][-1].AskPrice1
                    bid_call = self.md_list[index_call][-1].BidPrice1
                    bid_put = self.md_list[index_put][-1].BidPrice1
                    mid_call = (ask_call + ask_put) / 2
                    mid_put = (ask_put + bid_put) / 2

                    sign_ask = ask_call - ask_put + strike - S_ask
                    sign_put = bid_call - bid_put + strike - S_bid

                    threshold = 0.02
                    varity = 1

                    print(sign_ask, sign_put)
                    if sign_ask > threshold and sign_put > threshold:
                        # and sign_ask < max_threshold and sign_put < max_threshold:
                        print('call unbalance')

                        om = self.ins2om[ins_put.InstrumentID]
                        order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                     max(bid_put + 0.001, 0.001), 30)
                        self.send_input_order(order)
                        self.parity_order_list.append([time.time(), order])
                        om = self.ins2om[ins_call.InstrumentID]
                        order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                     max(ask_call - 0.001, 0.001), 30)
                        self.send_input_order(order)
                        self.parity_order_list.append([time.time(), order])
                        # time.sleep(0.01)

                    elif sign_ask < -threshold and sign_put < -threshold:
                        print('put unbalance')
                        om = self.ins2om[ins_call.InstrumentID]
                        order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                     max(bid_call + 0.001, 0.001), 20)
                        self.send_input_order(order)
                        self.parity_order_list.append([time.time(), order])

                        om = self.ins2om[ins_put.InstrumentID]
                        order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                     max(ask_put - 0.001, 0.001), 20)
                        self.send_input_order(order)
                        self.parity_order_list.append([time.time(), order])
                        # time.sleep(0.01)
                pos_hold = False
                if pos_hold:
                    new_order = []
                    for pos_time, order in self.parity_order_list:
                        ins = order.InstrumentID
                        stop_time = 3
                        if time.time() - pos_time > stop_time:
                            if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                                self.send_cancel_order(order)
                                print('order calceled')
                            elif order.OrderStatus == PHX_FTDC_OST_AllTraded:
                                print('Order Traded...')
                                pass
                            elif order.OrderStatus == PHX_FTDC_OST_Unknown:
                                new_order.append([pos_time, order])
                                print('Order waiting...')
                                continue
                            elif order.OrderStatus == PHX_FTDC_OST_Error or order.OrderStatus == PHX_FTDC_OST_Canceled:
                                continue
                        else:
                            new_order.append([pos_time, order])

                        index = self.ins2index[ins]
                        # if order.OrderStatus == PHX_FTDC_OST_AllTraded:
                        om = self.ins2om[ins]
                        spread = 3
                        if time.time() - pos_time < stop_time and order.VolumeTraded != 0:
                            cur_price = self.get_bid_ask(index).values[0]
                            benefit = 0.05
                            if order.Direction == "0":
                                if order.OffsetFlag == "0":  # buy open
                                    hold = False
                                    if index < 36:
                                        if order.LimitPrice < 10 - spread - self.instruments[index].StrikePrice:

                                            if om.get_long_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    else:
                                        if order.LimitPrice < self.instruments[index].StrikePrice - 10 - spread:

                                            if om.get_long_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True

                                    if ~hold and order.LimitPrice < cur_price[12] - benefit:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                            PHX_FTDC_OF_Close,
                                                                            min(cur_price[13], order.VolumeTraded))
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)
                                elif order.OffsetFlag == "1":  # buy close
                                    if order.LimitPrice > cur_price[2] + benefit:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                            PHX_FTDC_OF_Close,
                                                                            min(cur_price[3],
                                                                                order.VolumeTotalOriginal - order.VolumeTraded))
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)
                            elif order.Direction == "1":
                                if order.OffsetFlag == "0":  # sell open
                                    hold = False
                                    if index < 36:
                                        if order.LimitPrice > max(10 - spread - self.instruments[index].StrikePrice,
                                                                  0.02):

                                            if om.get_short_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    else:
                                        if order.LimitPrice > max(self.instruments[index].StrikePrice - 10 - spread,
                                                                  0.02):
                                            print(om.get_long_position_closeable())
                                            if om.get_short_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True

                                    if ~hold and order.LimitPrice > cur_price[2] + benefit:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                            PHX_FTDC_OF_Close,
                                                                            min(cur_price[3], order.VolumeTraded))
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)
                                elif order.OffsetFlag == "1":  # sell close
                                    if order.LimitPrice < cur_price[12] + benefit:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                            PHX_FTDC_OF_Close,
                                                                            min([13],
                                                                                order.VolumeTotalOriginal - order.VolumeTraded))
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)
                        elif time.time() - pos_time >= stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                            if order.Direction == "0":
                                if order.OffsetFlag == "0":  # buy open
                                    hold = False
                                    if index < 36:
                                        if order.LimitPrice < 10 - spread - self.instruments[index].StrikePrice:
                                            if om.get_long_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    else:
                                        if order.LimitPrice < self.instruments[index].StrikePrice - 10 - spread:
                                            if om.get_long_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    position_should_close = order.VolumeTraded

                                    if ~hold and position_should_close != 0:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                            PHX_FTDC_OF_Close,
                                                                            position_should_close)
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)

                                elif order.OffsetFlag == "1":  # buy close
                                    position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                    if position_should_close != 0:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                            PHX_FTDC_OF_Close,
                                                                            position_should_close)
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)

                            elif order.Direction == "1":
                                if order.OffsetFlag == "0":  # sell open
                                    hold = False
                                    if index < 36:
                                        if order.LimitPrice > max(10 - spread - self.instruments[index].StrikePrice,
                                                                  0.02):
                                            if om.get_short_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    else:
                                        if order.LimitPrice > max(self.instruments[index].StrikePrice - 10 - spread,
                                                                  0.02):
                                            if om.get_short_position_closeable() < 500:
                                                self.position_order_list.append([pos_time, order])
                                                hold = True
                                    position_should_close = order.VolumeTraded
                                    if ~hold and position_should_close != 0:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                            PHX_FTDC_OF_Close,
                                                                            position_should_close)
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)

                                elif order.OffsetFlag == "1":  # buy close
                                    position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                    if position_should_close != 0:
                                        om = self.ins2om[ins]
                                        order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                            PHX_FTDC_OF_Close,
                                                                            position_should_close)
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)

                        self.market_data_updated[self.ins2index[ins]] = False  # reset flag
                    self.is_any_updated = False  # reset flag
                    self.parity_order_list = new_order
            # mom
            elif st == 3:
                K = self.options_prices*2
                index = self.ins2index["UBIQ"]
                price = self.md_list[index][-1]
                S_ask = price.AskPrice1
                S_bid = price.BidPrice1
                S_ave = (S_ask + S_bid) / 2
                self.order_count = len(self.monoto_order_list)
                for i in range(self.inst_num - 1):
                    strike = K[i]
                    ins = self.instruments[i]
                    index = self.ins2index[ins.InstrumentID]
                    om = self.ins2om[ins.InstrumentID]
                    five = self.get_bid_ask(index).values[0]

                    sign_vol = five[3] + five[5] - five[13] - five[15]

                    varity1 = 1
                    varity2 = 1
                    if i < 36:
                        if five[12] > max(S_ave - strike, 0) + varity1:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[2] - 0.001, 0.001), 20)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            continue
                        elif five[2] < max(S_ave - strike, 0) - varity2:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[12] + 0.001, 0.001), 20)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            continue

                    else:
                        if five[12] > max(strike - S_ave, 0) + varity1:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[2] - 0.001, 0.001), 20)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.parity_order_list.append([time.time(), order])
                            continue
                        elif five[2] < max(strike - S_ave, 0) - varity2:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[12] + 0.001, 0.001), 20)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            continue

                    if five[2] - five[12] < 0.5:

                        thre = 0.3
                        if five[2] < max(S_ave - strike, 0) - thre and sign_vol < 0:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[12] + 0.001, 0.001), 15)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])

                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[14] / 3 + five[16] * 2 / 3, 0.001), 30)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            time.sleep(0.4)
                            while order.OrderStatus == PHX_FTDC_OST_Unknown:
                                time.sleep(0.01)
                            if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                                self.send_cancel_order(order)

                        elif five[12] > max(S_ave - strike, 0) + thre and sign_vol > 0:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[2] - 0.001, 0.001), 15)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])

                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[4] / 3 + five[6] * 2 / 3, 0.001), 30)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            time.sleep(0.4)
                            ti = time.time()
                            while order.OrderStatus == PHX_FTDC_OST_Unknown:
                                time.sleep(0.01)

                            if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                                self.send_cancel_order(order)
                    else:
                        if sign_vol < -20:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[12] + 0.001, 0.001), 15)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])

                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                         max(five[14] / 3 + five[16] * 2 / 3, 0.001), 30)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            time.sleep(0.4)
                            while order.OrderStatus == PHX_FTDC_OST_Unknown:
                                time.sleep(0.01)
                            if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                                self.send_cancel_order(order)
                        elif sign_vol > 20:
                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[2] - 0.001, 0.001), 15)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])

                            order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                         max(five[4] / 3 + five[6] * 2 / 3, 0.001), 30)
                            self.send_input_order(order)
                            self.order_count += 1
                            self.monoto_order_list.append([time.time(), order])
                            time.sleep(0.4)
                            while order.OrderStatus == PHX_FTDC_OST_Unknown:
                                time.sleep(0.005)
                            if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                                self.send_cancel_order(order)
                new_order = []
                for pos_time, order in self.parity_order_list:
                    ins = order.InstrumentID
                    stop_time = 4
                    if time.time() - pos_time > stop_time:
                        if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                            self.send_cancel_order(order)
                            print('order calceled')
                        elif order.OrderStatus == PHX_FTDC_OST_AllTraded:
                            print('Order Traded...')
                            pass
                        elif order.OrderStatus == PHX_FTDC_OST_Unknown:
                            new_order.append([pos_time, order])
                            print('Order waiting...')
                            continue
                        elif order.OrderStatus == PHX_FTDC_OST_Error or order.OrderStatus == PHX_FTDC_OST_Canceled:
                            continue
                    else:
                        new_order.append([pos_time, order])

                    index = self.ins2index[ins]
                    om = self.ins2om[ins]
                    spread = 3
                    if time.time() - pos_time < stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                        cur_price = self.get_bid_ask(index).values[0]
                        benefit = 0.05
                        if order.Direction == "0":
                            if order.OffsetFlag == "0":  # buy open

                                if order.LimitPrice < cur_price[12] - benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                        PHX_FTDC_OF_Close,
                                                                        min(cur_price[13], order.VolumeTraded))
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                            elif order.OffsetFlag == "1":  # buy close
                                if order.LimitPrice > cur_price[2] + benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                        PHX_FTDC_OF_Close,
                                                                        min(cur_price[3],
                                                                            order.VolumeTotalOriginal - order.VolumeTraded))
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                        elif order.Direction == "1":
                            if order.OffsetFlag == "0":  # sell open

                                if order.LimitPrice > cur_price[2] + benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                        PHX_FTDC_OF_Close,
                                                                        min(cur_price[3], order.VolumeTraded))
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                            elif order.OffsetFlag == "1":  # sell close
                                if order.LimitPrice < cur_price[12] + benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                        PHX_FTDC_OF_Close,
                                                                        min([13],
                                                                            order.VolumeTotalOriginal - order.VolumeTraded))
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                    elif time.time() - pos_time >= stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                        if order.Direction == "0":
                            if order.OffsetFlag == "0":  # buy open

                                position_should_close = order.VolumeTraded

                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                        PHX_FTDC_OF_Close,
                                                                        position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                            elif order.OffsetFlag == "1":  # buy close
                                position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                        PHX_FTDC_OF_Close,
                                                                        position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                        elif order.Direction == "1":
                            if order.OffsetFlag == "0":  # sell open
                                position_should_close = order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                        PHX_FTDC_OF_Close,
                                                                        position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                            elif order.OffsetFlag == "1":  # sell close
                                position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                        PHX_FTDC_OF_Close,
                                                                        position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                    self.market_data_updated[self.ins2index[ins]] = False  # reset flag
                self.is_any_updated = False  # reset flag
                self.parity_order_list = new_order
            # uniq
            elif st == 4:
                ins = self.instruments[72]
                om = self.ins2om[ins.InstrumentID]
                index = self.ins2index[ins.InstrumentID]
                five = self.get_bid_ask(index).values[0]
                try:
                    five_1 = self.get_bid_ask(index, 2).values[0]
                except:
                    five_1 = [0] * len(five)
                mom_up = 0
                mom_down = 0
                if five[2] < five_1[2]:
                    mom_down = five[3]
                elif five[2] == five_1[2]:
                    mom_down = five[3] - five_1[3]
                if five[12] > five_1[12]:
                    mom_up = five[13]
                elif five[12] == five_1[12]:
                    mom_up = five[13] - five_1[13]

                threshold = 5
                if mom_down > threshold and mom_up < -threshold:
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 five[2], 50)
                    self.send_input_order(order)
                    self.ubiq_order_list.append([time.time(), order])
                elif mom_down < -threshold and mom_up > threshold:
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 five[12], 50)
                    self.send_input_order(order)
                    self.ubiq_order_list.append([time.time(), order])
                elif mom_up - mom_down > threshold:
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Open,
                                                 five[12], 30)
                    self.send_input_order(order)
                    self.ubiq_order_list.append([time.time(), order])
                elif mom_up - mom_down < -threshold:
                    order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open,
                                                 five[2], 30)
                    self.send_input_order(order)
                    self.ubiq_order_list.append([time.time(), order])

                new_order = []
                for pos_time, order in self.ubiq_order_list:
                    ins = order.InstrumentID
                    stop_time = 2
                    if time.time() - pos_time > stop_time:
                        if order.OrderStatus == PHX_FTDC_OST_PartTradedQueueing or order.OrderStatus == PHX_FTDC_OST_NoTradeQueueing:
                            self.send_cancel_order(order)
                            print('order calceled')
                        elif order.OrderStatus == PHX_FTDC_OST_AllTraded:
                            print('Order Traded...')
                        elif order.OrderStatus == PHX_FTDC_OST_Unknown:
                            new_order.append([pos_time, order])
                            print('Order waiting...')
                            continue
                        elif order.OrderStatus == PHX_FTDC_OST_Error or order.OrderStatus == PHX_FTDC_OST_Canceled:
                            pass
                    else:
                        new_order.append([pos_time, order])

                    index = self.ins2index[ins]
                    om = self.ins2om[ins]
                    spread = 3
                    cur_price = self.get_bid_ask(index).values[0]
                    if time.time() - pos_time < stop_time and order.VolumeTraded != 0:

                        benefit = 0.02
                        if order.Direction == "0":
                            if order.OffsetFlag == "0":  # buy open

                                if order.LimitPrice < cur_price[12] - benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                       PHX_FTDC_OF_Close,
                                                                       cur_price[12], order.VolumeTraded)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                            elif order.OffsetFlag == "1":  # buy close
                                if order.LimitPrice > cur_price[2] + benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                       PHX_FTDC_OF_Close,
                                                                       cur_price[2],
                                                                       order.VolumeTotalOriginal - order.VolumeTraded)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                        elif order.Direction == "1":
                            if order.OffsetFlag == "0":  # sell open

                                if order.LimitPrice > cur_price[2] + benefit:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                       PHX_FTDC_OF_Close,
                                                                       cur_price[2], order.VolumeTraded)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)
                            elif order.OffsetFlag == "1":  # sell close
                                if order.LimitPrice < cur_price[12] + benefit:
                                    if order.VolumeTotalOriginal - order.VolumeTraded != 0:
                                        om = self.ins2om[ins]
                                        order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                           PHX_FTDC_OF_Close,
                                                                           cur_price[12],
                                                                           order.VolumeTotalOriginal - order.VolumeTraded)
                                        self.send_input_order(order_close)
                                        self.order_count += 1
                                        new_order.append([time.time(), order_close])
                                        # time.sleep(0.01)
                    elif time.time() - pos_time >= stop_time and order.OrderPriceType == PHX_FTDC_OPT_LimitPrice and order.VolumeTraded != 0:
                        if order.Direction == "0":
                            if order.OffsetFlag == "0":  # buy open

                                position_should_close = order.VolumeTraded

                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                       PHX_FTDC_OF_Close, cur_price[12],
                                                                       position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                            elif order.OffsetFlag == "1":  # sell close
                                position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                       PHX_FTDC_OF_Close, cur_price[12],
                                                                       position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                        elif order.Direction == "1":
                            if order.OffsetFlag == "0":  # sell open
                                position_should_close = order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy,
                                                                       PHX_FTDC_OF_Close, cur_price[2],
                                                                       position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                            elif order.OffsetFlag == "1":  # sell close
                                position_should_close = order.VolumeTotalOriginal - order.VolumeTraded
                                if position_should_close != 0:
                                    om = self.ins2om[ins]
                                    order_close = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell,
                                                                       PHX_FTDC_OF_Close, cur_price[12],
                                                                       position_should_close)
                                    self.send_input_order(order_close)
                                    self.order_count += 1
                                    new_order.append([time.time(), order_close])
                                    # time.sleep(0.01)

                    self.market_data_updated[self.ins2index[ins]] = False  # reset flag
                self.is_any_updated = False  # reset flag
                self.ubiq_order_list = new_order


if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option("-i", "--ip", dest="ip", help="server ip")
    parser.add_option("-p", "--port", dest="port", help="server ip")
    parser.add_option("-u", "--user_id", dest="user_id", help="user id")
    parser.add_option("-a", "--password", dest="password", help="password")
    (options, args) = parser.parse_args()
    server_ip = '106.120.131.90'
    # server_ip = '192.168.10.10'
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


    is_multi_thread=True

    if client.Init() and client.myInit(is_multi_thread):
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
                # client.monoto_adj()
                # time.sleep(1)
                # client.market_maker_strategy()##做市策略大概是因为下单太快被杀
                # client.run_strategy()
                # time.sleep(1)
                # client.put_call_parity()
                # time.sleep(1)
                # client.visual_position()
                time.sleep(1)
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