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
        logging.FileHandler('mackay_register.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class MackayChildHospitalRegistrar:
    def __init__(self):
        self.base_url = "https://www.mmh.org.tw/child"
        self.session = requests.Session()
        
        # 從環境變數讀取配置
        self.id_number = os.getenv('MACKAY_ID_NUMBER', '')
        self.birthday = os.getenv('MACKAY_BIRTHDAY', '')
        self.smtp_config = {
            'server': os.getenv('SMTP_SERVER', ''),
            'port': int(os.getenv('SMTP_PORT', '587')),
            'username': os.getenv('SMTP_USERNAME', ''),
            'password': os.getenv('SMTP_PASSWORD', ''),
            'sender': os.getenv('SMTP_SENDER', os.getenv('SMTP_USERNAME', '')),
            'recipient': os.getenv('MACKAY_NOTIFICATION_EMAIL', '')
        }
        
        # 驗證必要環境變數
        self.validate_environment()
        
        # 狀態文件
        self.state_file = 'mackay_state.json'
        
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
    
    def validate_environment(self):
        """驗證必要的環境變數"""
        required_vars = ['MACKAY_ID_NUMBER', 'MACKAY_BIRTHDAY']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            logger.error(f"缺少必要的環境變數: {', '.join(missing_vars)}")
            logger.error("請在 GitHub Secrets 中設置以下變數:")
            logger.error("MACKAY_ID_NUMBER - 身分證字號")
            logger.error("MACKAY_BIRTHDAY - 生日 (格式: YYYYMMDD)")
            sys.exit(1)
        
        logger.info("環境變數驗證通過")
    
    def load_state(self):
        """加載監控狀態"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"加載狀態文件失敗: {e}")
        
        # 默認狀態
        return {
            'last_notification_time': None,
            'pause_until': None,
            'notification_count': 0,
            'last_check': None
        }
    
    def save_state(self, state):
        """保存監控狀態"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存狀態文件失敗: {e}")
    
    def should_skip_check(self):
        """檢查是否需要跳過本次檢查"""
        state = self.load_state()
        pause_until = state.get('pause_until')
        
        if pause_until:
            try:
                pause_time = datetime.fromisoformat(pause_until)
                if datetime.now() < pause_time:
                    remaining = (pause_time - datetime.now()).total_seconds() / 60
                    logger.info(f"在暫停期內，跳過檢查。剩餘暫停時間: {remaining:.1f} 分鐘")
                    return True
                else:
                    # 暫停期已過，清除暫停狀態
                    state['pause_until'] = None
                    self.save_state(state)
            except Exception as e:
                logger.warning(f"解析暫停時間失敗: {e}")
                state['pause_until'] = None
                self.save_state(state)
        
        return False
    
    def init_session(self):
        """初始化會話，獲取必要的cookie"""
        try:
            logger.info("正在初始化會話...")
            
            # 先訪問首頁獲取cookie
            init_url = f"{self.base_url}/index.php"
            response = self.session.get(init_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            logger.info("首頁訪問成功")
            
            # 訪問register_action.php獲取更多cookie
            register_action_url = f"{self.base_url}/register_action.php"
            response = self.session.get(register_action_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            logger.info("register_action.php訪問成功")
            
            # 檢查是否有必要的cookie
            cookies = self.session.cookies.get_dict()
            logger.info(f"當前會話cookies: {cookies}")
            
        except requests.exceptions.Timeout:
            logger.error("初始化會話超時")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"初始化會話請求失敗: {e}")
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
                'strSchdate': appointment_data.get('date'),  # 格式: 2025/12/20
                'strSchap': appointment_data.get('session'),  # 1:上午, 2:下午, 3:夜間
                'strDept': appointment_data.get('dept_code'),  # 科別代碼
                'strDr': appointment_data.get('doctor_code'),  # 醫師代碼
                'strIdnoPassPortSel': '1',  # 身分證
                'txtID': appointment_data.get('id_number'),  # 身分證字號
                'txtBirth': appointment_data.get('birthday'),  # 生日: YYYYMMDD
                'txtwebword': appointment_data.get('captcha', ''),  # 驗證碼
            }
            
            logger.info(f"掛號表單數據: {form_data}")
            
            # 設置請求頭
            post_headers = self.headers.copy()
            post_headers.update({
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': f'{self.base_url}/register_action.php',
            })
            
            # 發送掛號請求
            register_url = f"{self.base_url}/registerdone.php"
            logger.info(f"發送掛號請求到: {register_url}")
            
            response = self.session.post(
                register_url,
                data=form_data,
                headers=post_headers,
                timeout=30
            )
            response.raise_for_status()
            
            # 記錄響應狀態
            logger.info(f"掛號請求響應狀態碼: {response.status_code}")
            
            # 解析結果
            return self.parse_result(response.text)
            
        except requests.exceptions.Timeout:
            logger.error("掛號請求超時")
            return {'success': False, 'error': '請求超時'}
        except requests.exceptions.RequestException as e:
            logger.error(f"掛號請求失敗: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"掛號過程中發生錯誤: {e}")
            return {'success': False, 'error': str(e)}
    
    def parse_result(self, html_content):
        """解析掛號結果頁面 - 增強版"""
        try:
            # 保存HTML用於調試
            debug_file = 'last_response.html'
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"已保存響應HTML到: {debug_file}")
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 獲取頁面文本
            page_text = soup.get_text()
            
            # 1. 先檢查明確的成功關鍵詞
            success_keywords = ['掛號成功', '預約成功', '掛號完成', '已掛號', '成功掛號']
            for keyword in success_keywords:
                if keyword in page_text:
                    logger.info(f"找到成功關鍵詞: {keyword}")
                    # 提取詳細信息
                    result = self.extract_details_from_page(soup, page_text)
                    result['success'] = True
                    result['full'] = False
                    return result
            
            # 2. 檢查滿號信息
            full_keywords = ['滿號', '請改掛', '已額滿', '額滿', '已掛滿']
            for keyword in full_keywords:
                if keyword in page_text:
                    logger.info(f"找到滿號關鍵詞: {keyword}")
                    return {
                        'success': False,
                        'full': True,
                        'status': '已滿號或無可用時段'
                    }
            
            # 3. 檢查錯誤信息（如驗證碼錯誤）
            error_keywords = ['驗證碼錯誤', '身份證錯誤', '生日錯誤', '資料錯誤']
            for keyword in error_keywords:
                if keyword in page_text:
                    logger.warning(f"找到錯誤關鍵詞: {keyword}")
                    return {
                        'success': False,
                        'error': keyword,
                        'full': False
                    }
            
            # 4. 查找特定的結果區域
            box_wrapper = soup.find('div', {'id': 'myprint'})
            if box_wrapper:
                list_items = box_wrapper.find_all('li')
                result = {}
                
                for item in list_items:
                    text = item.get_text(strip=True)
                    if '看診日期：' in text:
                        result['appointment_date'] = text.replace('看診日期：', '').strip()
                    elif '看診科別：' in text:
                        result['department'] = text.replace('看診科別：', '').strip()
                    elif '看診醫師：' in text:
                        result['doctor'] = text.replace('看診醫師：', '').strip()
                    elif '掛號結果：' in text:
                        result['status'] = text.replace('掛號結果：', '').strip()
                
                if 'status' in result:
                    logger.info(f"從myprint區域找到掛號結果: {result['status']}")
                    if '滿號' in result['status'] or '請改掛' in result['status']:
                        result['success'] = False
                        result['full'] = True
                    elif '成功' in result['status'] or '已掛號' in result['status']:
                        result['success'] = True
                        result['full'] = False
                    else:
                        result['success'] = False
                        result['full'] = False
                    return result
            
            # 5. 查找表格中的結果
            tables = soup.find_all('table')
            for table in tables:
                table_text = table.get_text(strip=True)
                if '掛號結果' in table_text or '看診日期' in table_text:
                    result = {}
                    rows = table.find_all('tr')
                    for row in rows:
                        cols = row.find_all('td')
                        if len(cols) >= 2:
                            key = cols[0].get_text(strip=True)
                            value = cols[1].get_text(strip=True)
                            if '日期' in key:
                                result['appointment_date'] = value
                            elif '科別' in key:
                                result['department'] = value
                            elif '醫師' in key:
                                result['doctor'] = value
                            elif '結果' in key:
                                result['status'] = value
                    
                    if 'status' in result:
                        logger.info(f"從表格找到掛號結果: {result['status']}")
                        if '滿號' in result['status'] or '請改掛' in result['status']:
                            result['success'] = False
                            result['full'] = True
                        elif '成功' in result['status'] or '已掛號' in result['status']:
                            result['success'] = True
                            result['full'] = False
                        else:
                            result['success'] = False
                            result['full'] = False
                        return result
            
            # 6. 如果以上都沒找到，檢查頁面是否有表單錯誤信息
            error_divs = soup.find_all(['div', 'p', 'span'], class_=['error', 'alert', 'warning'])
            if error_divs:
                error_msg = ' | '.join([div.get_text(strip=True) for div in error_divs[:3]])
                logger.warning(f"找到錯誤信息: {error_msg}")
                return {'success': False, 'error': f'頁面錯誤: {error_msg[:100]}'}
            
            # 7. 最後的備選方案：返回原始文本片段供調試
            text_preview = page_text.replace('\n', ' ').replace('\r', '').strip()[:500]
            logger.warning(f"無法解析結果，頁面內容: {text_preview}...")
            return {
                'success': False, 
                'error': f'無法解析結果，頁面內容: {text_preview}...'
            }
            
        except Exception as e:
            logger.error(f"解析結果失敗: {e}")
            # 保存錯誤頁面以便分析
            with open('error_response.html', 'w', encoding='utf-8') as f:
                f.write(html_content)
            return {'success': False, 'error': f'解析異常: {str(e)}'}
    
    def extract_details_from_page(self, soup, page_text):
        """從成功頁面提取詳細信息"""
        result = {}
        
        # 方法1：查找所有粗體標籤後面的內容
        strong_tags = soup.find_all('strong')
        for tag in strong_tags:
            tag_text = tag.get_text(strip=True)
            next_text = ''
            
            # 獲取下一個兄弟節點的文本
            next_sibling = tag.next_sibling
            while next_sibling and not next_text.strip():
                if hasattr(next_sibling, 'get_text'):
                    next_text = next_sibling.get_text(strip=True)
                elif isinstance(next_sibling, str):
                    next_text = next_sibling.strip()
                next_sibling = next_sibling.next_sibling
            
            if '日期' in tag_text and not result.get('appointment_date'):
                result['appointment_date'] = next_text
            elif '科別' in tag_text and not result.get('department'):
                result['department'] = next_text
            elif '醫師' in tag_text and not result.get('doctor'):
                result['doctor'] = next_text
        
        # 方法2：使用正則表達式提取常見格式
        patterns = [
            (r'看診日期[：:]?\s*([^\s]+)', 'appointment_date'),
            (r'科別[：:]?\s*([^\s]+)', 'department'),
            (r'醫師[：:]?\s*([^\s]+)', 'doctor'),
            (r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', 'appointment_date'),
        ]
        
        for pattern, key in patterns:
            match = re.search(pattern, page_text)
            if match and not result.get(key):
                result[key] = match.group(1)
        
        # 設置默認狀態
        result['status'] = '掛號成功'
        
        logger.info(f"從成功頁面提取的詳細信息: {result}")
        return result
    
    def send_email_notification(self, appointment_result):
        """發送郵件通知"""
        if not self.smtp_config['server']:
            logger.warning("未配置郵件設定，無法發送通知")
            return False
            
        try:
            # 創建郵件
            msg = MIMEMultipart()
            msg['From'] = self.smtp_config['sender']
            msg['To'] = self.smtp_config['recipient']
            msg['Subject'] = f"🎉 馬偕兒童醫院掛號成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            # 郵件內容
            body = f"""
            恭喜！馬偕兒童醫院掛號成功！
            
            詳細資訊：
            掛號狀態: 成功 ✓
            看診日期: {appointment_result.get('appointment_date', 'N/A')}
            看診科別: {appointment_result.get('department', 'N/A')}
            看診醫師: {appointment_result.get('doctor', 'N/A')}
            結果訊息: {appointment_result.get('status', 'N/A')}
            
            掛號時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            請記得準時就診！
            
            ---
            此為自動掛號系統通知
            """
            
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # 發送郵件
            server = smtplib.SMTP(self.smtp_config['server'], self.smtp_config['port'])
            server.starttls()
            server.login(self.smtp_config['username'], self.smtp_config['password'])
            server.send_message(msg)
            server.quit()
            
            logger.info("郵件通知已發送")
            return True
            
        except Exception as e:
            logger.error(f"發送郵件失敗: {e}")
            return False
    
    def batch_registration(self):
        """批量掛號 - 只嘗試指定的三個日期的上午診"""
        
        # 檢查是否需要跳過（在暫停期內）
        if self.should_skip_check():
            logger.info("在暫停期內，跳過本次檢查")
            return "skipped"
        
        # 初始化會話
        try:
            self.init_session()
        except Exception as e:
            logger.error(f"初始化會話失敗: {e}")
            return "init_failed"
        
        # 只嘗試這三個日期的上午診
        dates_to_try = [
            '2025/12/17',
            '2025/12/27',
            '2026/01/03',
        ]
        
        logger.info(f"將嘗試以下日期: {dates_to_try}")
        
        # 醫師列表 - 只嘗試丁瑋信醫師
        doctors_to_try = [
            {'code': '4561', 'name': '丁瑋信'},
        ]
        
        success_count = 0
        total_attempts = 0
        
        for date in dates_to_try:
            for doctor in doctors_to_try:
                total_attempts += 1
                
                # 準備掛號資料 - 只嘗試上午診 (session: '1')
                appointment_data = {
                    'date': date,
                    'session': '1',  # 修正：上午診代碼為'1'，不是'2'
                    'dept_code': '30',  # 小兒科
                    'doctor_code': doctor['code'],
                    'id_number': self.id_number,
                    'birthday': self.birthday,
                    'captcha': '',  # 注意：如果網站需要驗證碼，這裡需要處理
                }
                
                session_name = "上午診" if appointment_data['session'] == '1' else "下午診"
                logger.info(f"嘗試掛號 ({total_attempts}): {date} {doctor['name']} 醫師 {session_name}")
                
                # 執行掛號
                result = self.make_appointment(appointment_data)
                
                # 檢查結果
                if result.get('success'):
                    logger.info(f"✓ 成功掛到 {date} {doctor['name']} 醫師 {session_name}")
                    logger.info(f"詳細結果: {result}")
                    
                    # 發送郵件通知
                    if self.send_email_notification(result):
                        # 設置暫停期 - 避免短時間內重複檢查
                        state = self.load_state()
                        pause_until = datetime.now() + timedelta(hours=2)  # 暫停2小時
                        state['pause_until'] = pause_until.isoformat()
                        state['last_notification_time'] = datetime.now().isoformat()
                        state['notification_count'] = state.get('notification_count', 0) + 1
                        self.save_state(state)
                        logger.info(f"已設置暫停檢查直到: {pause_until.strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    success_count += 1
                    return "success"
                    
                elif result.get('full'):
                    logger.info(f"✗ {date} {doctor['name']} 醫師{session_name}已滿號")
                else:
                    error_msg = result.get('error', '未知錯誤')
                    logger.warning(f"? {date} {doctor['name']} 醫師掛號失敗: {error_msg}")
                
                # 避免請求過於頻繁
                time.sleep(2)
        
        logger.info(f"批量掛號完成。共嘗試 {total_attempts} 次，成功 {success_count} 次。")
        
        # 保存最後檢查時間
        state = self.load_state()
        state['last_check'] = datetime.now().isoformat()
        self.save_state(state)
        
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
            'success': "✅ 成功掛號！已發送郵件通知",
            'no_availability': "❌ 所有嘗試的日期都無可掛號時段",
        }
        
        logger.info(result_messages.get(result, f"執行結果: {result}"))
        
    except Exception as e:
        logger.error(f"程式執行過程中發生未預期的錯誤: {e}")
        return 1
    
    logger.info("=== 馬偕兒童醫院掛號監控結束 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())