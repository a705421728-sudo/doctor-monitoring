#!/usr/bin/env python3
"""
安全配置验证脚本
此脚本检查所有必要配置，并将结果通过邮件发送，避免公开日志泄露。
"""

import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import logging
import json

# 安全日志配置（不输出敏感信息到控制台）
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def collect_verification_report():
    """收集所有验证信息"""
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("马偕儿童医院挂号系统 - 配置安全验证报告")
    report_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 60 + "\n")

    # 1. 检查核心环境变量
    report_lines.append("【1/3】核心身份信息检查")
    report_lines.append("-" * 40)
    
    mackay_id = os.getenv('MACKAY_ID_NUMBER', '')
    mackay_birthday = os.getenv('MACKAY_BIRTHDAY', '')
    
    id_ok = len(mackay_id) == 10  # 假设身份证长度为10
    birthday_ok = len(mackay_birthday) == 7  # 假设生日格式为7位数字
    
    report_lines.append(f"✅ 环境变量 MACKAY_ID_NUMBER: {'已设置 (长度正确)' if id_ok else '❌ 未设置或格式异常'}")
    report_lines.append(f"✅ 环境变量 MACKAY_BIRTHDAY: {'已设置 (长度正确)' if birthday_ok else '❌ 未设置或格式异常'}")
    
    # 2. 检查邮件通知列表
    report_lines.append("\n【2/3】邮件通知列表检查")
    report_lines.append("-" * 40)
    
    email_list_str = os.getenv('MACKAY_NOTIFICATION_EMAIL', '')
    if email_list_str:
        email_list = [e.strip() for e in email_list_str.split(',') if e.strip()]
        report_lines.append(f"✅ 解析到 {len(email_list)} 个收件人邮箱:")
        for idx, email in enumerate(email_list, 1):
            report_lines.append(f"  {idx}. {email}")
    else:
        report_lines.append("❌ MACKAY_NOTIFICATION_EMAIL 未设置")
        email_list = []

    # 3. 检查SMTP配置（关键组件）
    report_lines.append("\n【3/3】SMTP邮件服务器配置检查")
    report_lines.append("-" * 40)
    
    smtp_vars = {
        'SMTP_SERVER': os.getenv('SMTP_SERVER', ''),
        'SMTP_USERNAME': os.getenv('SMTP_USERNAME', ''),
        'SMTP_SENDER': os.getenv('SMTP_SENDER', os.getenv('SMTP_USERNAME', '')),
    }
    
    smtp_ok = True
    for var_name, var_value in smtp_vars.items():
        if var_value:
            # 显示部分信息（安全）
            display_value = var_value[:3] + '***' + var_value[-4:] if len(var_value) > 7 else '***'
            report_lines.append(f"✅ {var_name}: {display_value}")
        else:
            report_lines.append(f"❌ {var_name}: 未设置")
            smtp_ok = False
    
    # 总结
    report_lines.append("\n" + "=" * 60)
    report_lines.append("验证总结")
    report_lines.append("-" * 40)
    
    all_ok = all([id_ok, birthday_ok, bool(email_list), smtp_ok])
    if all_ok:
        report_lines.append("✅ **所有配置检查通过！**")
        report_lines.append("✅ 系统已就绪，可以执行挂号监控任务。")
    else:
        report_lines.append("⚠️ **部分配置需要检查。**")
        if not id_ok or not birthday_ok:
            report_lines.append("   请检查 GitHub Secrets 中的 MACKAY_ID_NUMBER 和 MACKAY_BIRTHDAY")
        if not email_list:
            report_lines.append("   请检查 MACKAY_NOTIFICATION_EMAIL 邮箱列表")
        if not smtp_ok:
            report_lines.append("   请检查 SMTP_* 相关配置")
    
    return "\n".join(report_lines), all_ok, email_list[0] if email_list else None

def send_verification_email(report_content, recipient_email):
    """发送验证报告到指定邮箱"""
    smtp_server = os.getenv('SMTP_SERVER', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_username = os.getenv('SMTP_USERNAME', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')
    smtp_sender = os.getenv('SMTP_SENDER', smtp_username)
    
    if not all([smtp_server, smtp_username, smtp_password, recipient_email]):
        logger.error("❌ 邮件配置不完整，无法发送验证报告")
        return False
    
    try:
        # 创建邮件
        msg = MIMEMultipart()
        msg['From'] = smtp_sender
        msg['To'] = recipient_email
        msg['Subject'] = f"✅ 挂号系统配置验证报告 - {datetime.now().strftime('%Y-%m-%d')}"
        
        # 邮件正文
        body = f"""
您好，

这是您的马偕儿童医院自动挂号系统的配置验证报告。
请查阅以下详细信息：

{report_content}

---
本邮件由自动化验证系统生成
GitHub Repository: {os.getenv('GITHUB_REPOSITORY', 'N/A')}
运行ID: {os.getenv('GITHUB_RUN_ID', 'N/A')}
        """
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # 发送邮件
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        logger.info(f"✅ 验证报告已安全发送至: {recipient_email}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 发送验证邮件失败: {e}")
        return False

def main():
    """主函数"""
    logger.info("开始安全配置验证...")
    
    # 收集验证报告
    report, all_ok, primary_email = collect_verification_report()
    
    # 在公共日志中只输出无害的摘要
    if all_ok:
        logger.info("✅ 所有环境变量检查通过（详细报告已发送至您的邮箱）")
    else:
        logger.info("⚠️  部分配置需要检查（详细报告已发送至您的邮箱）")
    
    # 将详细报告发送至邮箱（不在公共日志显示）
    if primary_email:
        send_verification_email(report, primary_email)
    else:
        # 如果没有设置通知邮箱，尝试使用发件人邮箱
        sender_email = os.getenv('SMTP_SENDER', os.getenv('SMTP_USERNAME', ''))
        if sender_email:
            send_verification_email(report, sender_email)
        else:
            # 如果连发件人邮箱都没有，只能将关键摘要打印到日志（不含敏感信息）
            safe_summary = report.split("验证总结")[0] + "\n【验证总结】\n" + ("✅ 配置就绪" if all_ok else "⚠️ 需要检查")
            logger.info(safe_summary)
    
    # 返回适当的退出码
    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    main()