import requests
import re
from bs4 import BeautifulSoup
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import logging
import os
import sys
import json

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)  # 只輸出到控制台，不在 GitHub Actions 產生檔案
    ]
)
logger = logging.getLogger(__name__)


class MackayChildHospitalRegistrar:
    def __init__(self):
        self.base_url = "https://www.mmh.org.tw/child"
        self.session = requests.Session()
        
        # 支援本地測試和GitHub環境
        self.load_config()
        
        # 設定 User-Agent 模擬瀏覽器
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://www.mmh.org.tw',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # 記錄是否已發送通知（僅限本次執行）
        self.notification_sent = False
    
    def load_config(self):
        """加載配置：優先使用環境變數，本地測試可用config.json"""
        # 從環境變數讀取（GitHub用）
        self.id_number = os.getenv('MACKAY_ID_NUMBER', '')
        self.birthday = os.getenv('MACKAY_BIRTHDAY', '')
        
        # 如果環境變數為空，嘗試從本地config.json讀取
        if not self.id_number or not self.birthday:
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.id_number = config.get('MACKAY_ID_NUMBER', self.id_number)
                    self.birthday = config.get('MACKAY_BIRTHDAY', self.birthday)
                    logger.info("從config.json讀取配置")
            except FileNotFoundError:
                logger.warning("未找到config.json，將使用環境變數或預設值")
        
        # 郵件配置 - 從環境變數或config.json讀取
        smtp_config_from_env = {
            'server': os.getenv('SMTP_SERVER', ''),
            'port': int(os.getenv('SMTP_PORT', '587')),
            'username': os.getenv('SMTP_USERNAME', ''),
            'password': os.getenv('SMTP_PASSWORD', ''),
            'sender': os.getenv('SMTP_SENDER', os.getenv('SMTP_USERNAME', '')),
            'recipient': os.getenv('MACKAY_NOTIFICATION_EMAIL', '')
        }
        
        # 嘗試從config.json讀取郵件配置
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 覆蓋環境變數中的配置
                for key in ['SMTP_SERVER', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'MACKAY_NOTIFICATION_EMAIL']:
                    if key in config:
                        if key == 'SMTP_PORT':
                            smtp_config_from_env['port'] = int(config[key])
                        elif key == 'SMTP_SERVER':
                            smtp_config_from_env['server'] = config[key]
                        elif key == 'SMTP_USERNAME':
                            smtp_config_from_env['username'] = config[key]
                            if not smtp_config_from_env['sender']:
                                smtp_config_from_env['sender'] = config[key]
                        elif key == 'SMTP_PASSWORD':
                            smtp_config_from_env['password'] = config[key]
                        elif key == 'MACKAY_NOTIFICATION_EMAIL':
                            smtp_config_from_env['recipient'] = config[key]
                
                # 特別處理SMTP_SENDER
                if 'SMTP_SENDER' in config:
                    smtp_config_from_env['sender'] = config['SMTP_SENDER']
                
                logger.info("從config.json讀取郵件配置")
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("未找到config.json或格式錯誤，將使用環境變數郵件配置")
        
        self.smtp_config = smtp_config_from_env
        
        # 驗證必要配置
        self.validate_config()
    
    def validate_config(self):
        """驗證必要的配置"""
        errors = []
        
        # 驗證掛號必要配置
        if not self.id_number:
            errors.append("MACKAY_ID_NUMBER (身分證字號)")
        if not self.birthday:
            errors.append("MACKAY_BIRTHDAY (生日)")
        
        if errors:
            error_msg = f"缺少必要配置: {', '.join(errors)}"
            logger.error(error_msg)
            logger.error("請設置環境變數或創建 config.json 文件")
            sys.exit(1)
        
        logger.info("配置驗證通過")
    
    def init_session(self):
        """初始化會話，獲取必要的cookie"""
        try:
            # 訪問register_action.php獲取cookie
            register_action_url = f"{self.base_url}/register_action.php"
            response = self.session.get(register_action_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
        except requests.exceptions.Timeout:
            logger.error("初始化會話超時")
            raise
        except Exception as e:
            logger.error(f"初始化會話失敗: {e}")
            raise
    
    def make_appointment(self, appointment_data):
        """
        執行掛號
        appointment_data: 包含掛號資訊的字典
        """
        try:
            # 準備表單數據
            form_data = {
                'workflag': 'registernow',
                'strSchdate': appointment_data.get('date'),
                'strSchap': appointment_data.get('session'),  # 1:上午, 2:下午
                'strDept': appointment_data.get('dept_code'),
                'strDr': appointment_data.get('doctor_code'),
                'strIdnoPassPortSel': '1',
                'txtID': appointment_data.get('id_number'),
                'txtBirth': appointment_data.get('birthday'),
                'txtwebword': appointment_data.get('captcha', ''),
            }
            
            # 設置請求頭
            post_headers = self.headers.copy()
            post_headers.update({
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': f'{self.base_url}/register_action.php',
            })
            
            # 發送掛號請求
            register_url = f"{self.base_url}/registerdone.php"
            logger.info(f"嘗試掛號: {appointment_data.get('date')} {appointment_data.get('session_name')}")
            
            response = self.session.post(
                register_url,
                data=form_data,
                headers=post_headers,
                timeout=30
            )
            response.raise_for_status()
            
            # 解析結果
            return self.parse_result(response.text, appointment_data)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"掛號請求失敗: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"掛號過程中發生錯誤: {e}")
            return {'success': False, 'error': str(e)}
    
    def parse_result(self, html_content, appointment_data):
        """解析掛號結果頁面 - 簡化版"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 獲取頁面文本
            page_text = soup.get_text()
            
            # 1. 檢查滿號信息
            if '滿號' in page_text or '請改掛' in page_text:
                return {'success': False, 'full': True, 'status': '已滿號'}
            
            # 2. 檢查成功信息 - 主要檢查「預約掛號成功」
            if '預約掛號成功' in page_text:
                result = {'success': True, 'full': False, 'status': '掛號成功'}
                return result
            
            # 3. 檢查健兒門診（當作測試成功）
            if '健兒門診' in page_text:
                result = {
                    'success': True,
                    'full': False,
                    'status': '健兒門診掛號成功',
                    'appointment_date': appointment_data['date'].replace('/', '-')
                }
                return result
            
            # 4. 檢查錯誤信息
            if '找不到醫師看診資料' in page_text:
                return {'success': False, 'error': '找不到醫師看診資料', 'full': False}
            
            # 5. 其他情況
            return {'success': False, 'error': '無法解析結果', 'full': False}
            
        except Exception as e:
            logger.error(f"解析結果失敗: {e}")
            return {'success': False, 'error': f'解析異常: {str(e)}'}
    
    def send_email_notification(self, appointment_result):
        """發送郵件通知 - 修正多個收件人問題"""
        try:
            # 檢查郵件配置是否完整
            required_configs = ['server', 'username', 'password', 'recipient']
            missing_configs = []
            
            for config in required_configs:
                if not self.smtp_config.get(config):
                    missing_configs.append(config)
            
            if missing_configs:
                logger.error(f"郵件配置不完整，缺少: {', '.join(missing_configs)}")
                return False
            
            # 創建郵件
            msg = MIMEMultipart()
            
            # 設置發件人
            sender = self.smtp_config.get('sender', self.smtp_config['username'])
            msg['From'] = sender
            
            # 處理多個收件人
            recipients = self.smtp_config['recipient']
            recipient_list = [email.strip() for email in recipients.split(',')]
            msg['To'] = ', '.join(recipient_list)
            
            # 郵件主題
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            subject = f"🎉 馬偕兒童醫院掛號成功 - {current_time}"
            msg['Subject'] = subject
            
            # 郵件內容 - 簡化版
            appointment_date = appointment_result.get('appointment_date', 'N/A')
            status = appointment_result.get('status', '成功')
            
            body = f"""
馬偕兒童醫院掛號成功！

掛號狀態: {status}
看診日期: {appointment_date}
掛號時間: {current_time}

請記得準時就診！

---
此為自動掛號系統通知
"""
            
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # 發送郵件 - 修正: 使用 sendmail 而不是 send_message
            logger.info(f"正在發送郵件通知給 {len(recipient_list)} 個收件人...")
            server = smtplib.SMTP(self.smtp_config['server'], self.smtp_config['port'])
            server.starttls()
            server.login(self.smtp_config['username'], self.smtp_config['password'])
            
            # 使用 sendmail 確保所有收件人都能收到
            server.sendmail(sender, recipient_list, msg.as_string())
            server.quit()
            
            logger.info(f"郵件通知已發送給 {len(recipient_list)} 個收件人")
            self.notification_sent = True
            return True
            
        except Exception as e:
            logger.error(f"發送郵件失敗: {e}")
            return False
    
    def batch_registration(self):
        """批量掛號 - 添加簡單重試機制"""
        # 初始化會話
        try:
            self.init_session()
        except Exception as e:
            logger.error(f"初始化會話失敗: {e}")
            return "init_failed"
        
        # 每個日期可以指定不同的時段
        appointments_to_try = [
            #{'date': '2025/12/17', 'session': '2', 'session_name': '下午診'},
            {'date': '2026/01/31', 'session': '1', 'session_name': '上午診'},
            {'date': '2025/12/27', 'session': '1', 'session_name': '上午診'},
            {'date': '2026/01/03', 'session': '1', 'session_name': '上午診'},
            {'date': '2026/01/10', 'session': '1', 'session_name': '上午診'},
            {'date': '2026/01/17', 'session': '1', 'session_name': '上午診'},
            {'date': '2026/01/24', 'session': '1', 'session_name': '上午診'},
        ]
        
        # 醫師列表
        doctors_to_try = [
            {'code': '4561', 'name': '丁瑋信'},
        ]
        
        logger.info(f"將嘗試以下掛號時段:")
        for appt in appointments_to_try:
            logger.info(f"  {appt['date']} {appt['session_name']}")
        
        # 簡單重試機制：在單次執行中嘗試3輪
        for retry_round in range(1, 61):  # 總共嘗試3輪
            logger.info(f"=== 第 {retry_round}/60 輪嘗試 ===")
            
            success_count = 0
            total_attempts = 0
            
            for appointment in appointments_to_try:
                for doctor in doctors_to_try:
                    total_attempts += 1
                    
                    # 準備掛號資料
                    appointment_data = {
                        'date': appointment['date'],
                        'session': appointment['session'],
                        'session_name': appointment['session_name'],
                        'dept_code': '30',  # 小兒科
                        'doctor_code': doctor['code'],
                        'id_number': self.id_number,
                        'birthday': self.birthday,
                        'captcha': '',
                    }
                    
                    logger.info(f"嘗試掛號 ({total_attempts}): {appointment['date']} {doctor['name']} 醫師 {appointment['session_name']}")
                    
                    # 執行掛號
                    result = self.make_appointment(appointment_data)
                    
                    # 檢查結果
                    if result.get('success'):
                        logger.info(f"✓ 成功掛到 {appointment['date']} {doctor['name']} 醫師 {appointment['session_name']}")
                        
                        # 發送郵件通知
                        email_sent = self.send_email_notification(result)
                        
                        if email_sent:
                            logger.info("郵件通知已發送")
                        else:
                            logger.warning("郵件發送失敗")
                        
                        success_count += 1
                        return "success"
                        
                    elif result.get('full'):
                        logger.info(f"✗ {appointment['date']} {doctor['name']} 醫師{appointment['session_name']}已滿號")
                    else:
                        error_msg = result.get('error', '未知錯誤')
                        logger.info(f"✗ {appointment['date']} {doctor['name']} 醫師掛號失敗: {error_msg}")
                    
                    # 避免請求過於頻繁
                    time.sleep(2)
            
            # 如果不是最後一輪，等待3分鐘再試下一輪
            if retry_round < 60:
                logger.info(f"等待3分鐘後進行第 {retry_round+1}/60 輪嘗試...")
                time.sleep(180)  # 3分鐘
        
        logger.info(f"批量掛號完成。共嘗試5輪，無可掛號時段。")
        return "no_availability"


def main():
    """主程式"""
    logger.info("=== 開始馬偕兒童醫院掛號監控 ===")
    
    try:
        # 創建掛號器實例
        registrar = MackayChildHospitalRegistrar()
        
        # 執行批量掛號
        result = registrar.batch_registration()
        
        # 記錄結果
        result_messages = {
            'skipped': "⏸️ 在暫停期內，跳過檢查",
            'init_failed': "❌ 初始化會話失敗",
            'success': "✅ 成功掛號！",
            'success_and_exit': "✅ 成功掛號！程式將退出",
            'no_availability': "❌ 無可掛號時段",
        }
        
        logger.info(result_messages.get(result, f"執行結果: {result}"))
        
    except Exception as e:
        logger.error(f"程式執行過程中發生錯誤: {e}")
        return 1
    
    logger.info("=== 馬偕兒童醫院掛號監控結束 ===")
    return 0


if __name__ == "__main__":

    sys.exit(main())
