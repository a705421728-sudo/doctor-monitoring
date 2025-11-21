from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import time
import logging
from datetime import datetime
import sys
import os

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('doctor_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class DoctorMonitor:
    def __init__(self, urls, email_config=None):
        self.urls = urls if isinstance(urls, list) else [urls]
        self.email_config = email_config
        self.driver = None
        self.setup_driver()
        
        # 建立醫師姓名與URL的對應關係
        self.doctor_url_mapping = self.create_doctor_url_mapping()
    
    def create_doctor_url_mapping(self):
        """建立醫師姓名與URL的對應關係"""
        mapping = {}
        for url in self.urls:
            if 'DOC3208F' in url:
                mapping['尤香玉'] = url
            """            
            elif 'DOC3491G' in url:
                mapping['周建成'] = url
            """
            
        return mapping
    
    def setup_driver(self):
        """設置Chrome瀏覽器驅動"""
        try:
            chrome_options = Options()
            
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')
            
            # 在 GitHub Actions 中，Chrome 和 ChromeDriver 已經設置好
            # 直接使用系統的 ChromeDriver
            service = Service()
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logging.info("瀏覽器驅動初始化成功")
            
        except Exception as e:
            logging.error(f"瀏覽器驅動初始化失敗: {e}")
            sys.exit(1)
    
    def parse_doctor_schedule(self, current_url):
        """解析醫師排班表格"""
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "table_list"))
            )
            
            schedule_tables = self.driver.find_elements(By.CLASS_NAME, "table_list")
            available_slots = []
            
            for table in schedule_tables:
                table_class = table.get_attribute('class') or ''
                
                if 'reg_return_table' in table_class:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    
                    for row_idx, row in enumerate(rows):
                        if row_idx == 0:
                            continue
                            
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) >= 7:
                            clinic_type = cells[0].text.strip()
                            date = cells[1].text.strip()
                            week_day = cells[2].text.strip()
                            time_slot = cells[3].text.strip()
                            doctor_name = cells[4].text.strip()
                            room = cells[5].text.strip()
                            status = cells[6].text.strip()
                            
                            if (status in ['可掛號', '可選擇'] and 
                                doctor_name != '代診醫師' and 
                                '代診' not in doctor_name):
                                
                                available_slots.append({
                                    'clinic_type': clinic_type,
                                    'date': date,
                                    'week_day': week_day,
                                    'time_slot': time_slot,
                                    'doctor_name': doctor_name,
                                    'room': room,
                                    'status': status,
                                    'url': current_url  # 添加當前URL
                                })
            
            return available_slots
            
        except Exception as e:
            logging.error(f"解析排班表格時出錯: {e}")
            return []
    
    def check_doctor_availability(self, url):
        """檢查單個醫師的可用性"""
        try:
            logging.info(f"檢查: {url}")
            self.driver.get(url)
            time.sleep(5)
            
            page_source = self.driver.page_source
            if '醫師' not in page_source and '醫生' not in page_source:
                logging.warning(f"頁面內容可能不正確: {url}")
                return None
            
            available_slots = self.parse_doctor_schedule(url)
            return available_slots
            
        except Exception as e:
            logging.error(f"檢查醫師可用性時出錯: {e}")
            return None
    
    def send_email_notification(self, available_slots):
        """發送郵件通知 - 支援多個收件人"""
        if not self.email_config:
            logging.warning("未配置郵件設定，無法發送通知")
            return False
            
        try:
            # 提取所有可掛號的醫師姓名
            doctors = list(set([slot['doctor_name'] for slot in available_slots]))
            
            # 生成主旨 - 包含醫師姓名和可掛號時段數量
            if len(doctors) == 1:
                subject = f"醫師可掛號通知 - {doctors[0]} ({len(available_slots)}個時段)"
            else:
                subject = f"醫師可掛號通知 - {', '.join(doctors)} ({len(available_slots)}個時段)"
            
            # 生成詳細的郵件內容，改善排版
            content = f"""您好，

監測到以下醫師時段可以掛號，詳細資訊如下：

"""
            # 按醫師分組時段
            doctor_slots = {}
            for slot in available_slots:
                doctor_name = slot['doctor_name']
                if doctor_name not in doctor_slots:
                    doctor_slots[doctor_name] = []
                doctor_slots[doctor_name].append(slot)
            
            # 為每個醫師生成專屬區塊
            for i, (doctor_name, slots) in enumerate(doctor_slots.items(), 1):
                # 使用slot中的URL，確保連結正確
                doctor_url = slots[0]['url'] if slots else ""
                
                content += f"【{doctor_name}】\n"
                content += f"掛號連結: {doctor_url}\n\n"
                content += "可掛號時段:\n"
                
                for j, slot in enumerate(slots, 1):
                    content += f"  {j}. {slot['date']} ({slot['week_day']}) {slot['time_slot']}\n"
                    content += f"     診間: {slot['room']} | 診別: {slot['clinic_type']}\n"
                
                if i < len(doctor_slots):  # 不是最後一個醫師，添加分隔線
                    content += "\n" + "="*50 + "\n\n"
            
            content += f"\n監測時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            content += "請盡快前往掛號，以免向隅！\n\n"
            content += "（此郵件由自動監控程序發送）"
            
            # 創建郵件
            message = MIMEText(content, 'plain', 'utf-8')
            message['From'] = Header(self.email_config['from_email'], 'utf-8')
            
            # 處理多個收件人
            if isinstance(self.email_config['to_email'], list):
                # 多個收件人 - 用逗號分隔
                to_emails = ", ".join(self.email_config['to_email'])
                message['To'] = Header(to_emails, 'utf-8')
                recipients = self.email_config['to_email']
            else:
                # 單一收件人
                message['To'] = Header(self.email_config['to_email'], 'utf-8')
                recipients = [self.email_config['to_email']]
            
            message['Subject'] = Header(subject, 'utf-8')
            
            # 發送郵件
            with smtplib.SMTP_SSL(self.email_config['smtp_server'], self.email_config['smtp_port']) as server:
                server.login(self.email_config['from_email'], self.email_config['password'])
                server.sendmail(
                    self.email_config['from_email'],
                    recipients,  # 使用 recipients 列表
                    message.as_string()
                )
            
            logging.info(f"郵件通知發送成功，收件人: {recipients}")
            return True
            
        except Exception as e:
            logging.error(f"發送郵件時出錯: {e}")
            return False
    
    def check_all_doctors(self):
        """檢查所有醫師的可用性"""
        all_available_slots = []
        
        for url in self.urls:
            available_slots = self.check_doctor_availability(url)
            
            if available_slots is not None:
                all_available_slots.extend(available_slots)
                if available_slots:
                    logging.info(f"發現 {len(available_slots)} 個可掛號時段")
                else:
                    logging.info("當前無可掛號時段")
            else:
                logging.warning(f"檢查 {url} 時出錯")
            
            time.sleep(2)
        
        return all_available_slots
    
    def monitor(self, check_interval=300):
        """開始監控"""
        logging.info(f"開始監控醫師狀態，檢查間隔: {check_interval}秒")
        logging.info(f"監控網址: {', '.join(self.urls)}")
        
        error_count = 0
        
        try:
            while True:
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logging.info(f"[{current_time}] 檢查醫師狀態...")
                
                try:
                    available_slots = self.check_all_doctors()
                    
                    if available_slots:
                        logging.info(f"🎉 發現 {len(available_slots)} 個可掛號時段！")
                        
                        # 發送郵件通知
                        if self.email_config:
                            self.send_email_notification(available_slots)
                    
                    error_count = 0  # 重置錯誤計數
                    
                except Exception as e:
                    error_count += 1
                    logging.error(f"檢查過程中出錯: {e}")
                    
                    # 如果連續錯誤多次，重新啟動瀏覽器
                    if error_count >= 3:
                        logging.warning("連續錯誤多次，重新啟動瀏覽器...")
                        if self.driver:
                            self.driver.quit()
                        self.setup_driver()
                        error_count = 0
                
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            logging.info("監控程序被用戶中斷")
        except Exception as e:
            logging.error(f"監控過程中出錯: {e}")
        finally:
            if self.driver:
                self.driver.quit()
                logging.info("瀏覽器已關閉")

def main():
    # 配置信息 - 支援多個收件人
    config = {
        'urls': [
            'https://www6.vghtpe.gov.tw/reg/docTimetable.do?docid=DOC3208F',  # 尤香玉醫師
            #'https://www6.vghtpe.gov.tw/reg/docTimetable.do?docid=DOC3491G'   # 周建成醫師
        ],
        'email_config': {
            'smtp_server': 'smtp.gmail.com',      # 郵件服務器
            'smtp_port': 465,                     # SSL端口
            'from_email': 'ben.liu@ennowell.com', # 發件郵箱
            'to_email': [                         # 多個收件人 - 使用列表
                'ben.liu@ennowell.com',
                'a705421728@gmail.com',
                'anna73761103@gmail.com'
            ],
            'password': 'gjeacilwxyrxukin'        # 郵箱密碼或應用專用密碼
        },
        'check_interval': 60  # 檢查間隔（秒）
    }
    
    # 創建監控器
    monitor = DoctorMonitor(config['urls'], config['email_config'])
    
    # 開始監控
    monitor.monitor(config['check_interval'])

if __name__ == "__main__":
    main()