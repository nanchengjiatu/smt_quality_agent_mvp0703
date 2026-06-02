CREATE TABLE spi_result (
    id BIGSERIAL PRIMARY KEY,
    batch_no VARCHAR(64),
    work_order VARCHAR(64) NOT NULL,
    product_name VARCHAR(128) NOT NULL,
    board_sn VARCHAR(128) NOT NULL,
    inspect_time TIMESTAMP NOT NULL,
    machine VARCHAR(64),
    side VARCHAR(16),
    component VARCHAR(64) NOT NULL,
    pad VARCHAR(64) NOT NULL,
    x NUMERIC(12, 4),
    y NUMERIC(12, 4),
    volume NUMERIC(12, 4),
    volume_upper NUMERIC(12, 4),
    volume_lower NUMERIC(12, 4),
    area NUMERIC(12, 4),
    area_upper NUMERIC(12, 4),
    area_lower NUMERIC(12, 4),
    height NUMERIC(12, 4),
    height_upper NUMERIC(12, 4),
    height_lower NUMERIC(12, 4),
    raw_ng_type VARCHAR(64),
    volume_deviation_percent NUMERIC(8, 2),
    area_deviation_percent NUMERIC(8, 2),
    height_deviation_percent NUMERIC(8, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_spi_result_batch ON spi_result(batch_no);
CREATE INDEX idx_spi_result_board ON spi_result(work_order, product_name, board_sn);
CREATE INDEX idx_spi_result_point ON spi_result(work_order, product_name, component, pad, inspect_time);
CREATE INDEX idx_spi_result_time ON spi_result(inspect_time);

CREATE TABLE spi_abnormal (
    abnormal_id BIGSERIAL PRIMARY KEY,
    spi_result_id BIGINT REFERENCES spi_result(id),
    work_order VARCHAR(64) NOT NULL,
    product_name VARCHAR(128) NOT NULL,
    board_sn VARCHAR(128) NOT NULL,
    inspect_time TIMESTAMP NOT NULL,
    machine VARCHAR(64),
    side VARCHAR(16),
    component VARCHAR(64) NOT NULL,
    pad VARCHAR(64) NOT NULL,
    defect_type VARCHAR(32) NOT NULL,
    main_metric VARCHAR(32),
    actual_value NUMERIC(12, 4),
    upper_limit NUMERIC(12, 4),
    lower_limit NUMERIC(12, 4),
    deviation_percent NUMERIC(8, 2),
    abnormal_pattern VARCHAR(64),
    risk_level VARCHAR(32),
    repeat_count INT DEFAULT 1,
    affected_pad_count INT DEFAULT 1,
    affected_component_count INT DEFAULT 1,
    board_abnormal_ratio NUMERIC(8, 4),
    root_cause_guess TEXT,
    suggested_action TEXT,
    status VARCHAR(32) DEFAULT '待处理',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_spi_abnormal_status CHECK (status IN ('待处理', '处理中', '待复测', '已关闭', '忽略'))
);

CREATE INDEX idx_spi_abnormal_time ON spi_abnormal(inspect_time);
CREATE INDEX idx_spi_abnormal_point ON spi_abnormal(work_order, product_name, component, pad, defect_type);
CREATE INDEX idx_spi_abnormal_board ON spi_abnormal(work_order, product_name, board_sn);
CREATE INDEX idx_spi_abnormal_status ON spi_abnormal(status);
CREATE INDEX idx_spi_abnormal_risk ON spi_abnormal(risk_level);
CREATE INDEX idx_spi_abnormal_pattern ON spi_abnormal(abnormal_pattern);

CREATE TABLE quality_case (
    case_id BIGSERIAL PRIMARY KEY,
    abnormal_id BIGINT REFERENCES spi_abnormal(abnormal_id),
    work_order VARCHAR(64) NOT NULL,
    product_name VARCHAR(128) NOT NULL,
    board_sn VARCHAR(128) NOT NULL,
    machine VARCHAR(64),
    component VARCHAR(64),
    pad VARCHAR(64),
    defect_type VARCHAR(32) NOT NULL,
    abnormal_pattern VARCHAR(64),
    risk_level VARCHAR(32),
    evidence_summary TEXT,
    root_cause_guess TEXT,
    suggested_action TEXT,
    actual_cause VARCHAR(128),
    actual_action VARCHAR(128),
    owner VARCHAR(64),
    status VARCHAR(32) DEFAULT '待处理',
    recheck_board_sn VARCHAR(128),
    recheck_result VARCHAR(64) DEFAULT '未复测',
    effective BOOLEAN,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    CONSTRAINT chk_quality_case_status CHECK (status IN ('待处理', '处理中', '待复测', '已关闭', '复发', '升级分析')),
    CONSTRAINT chk_quality_case_recheck CHECK (recheck_result IN ('未复测', 'OK', 'NG'))
);

CREATE INDEX idx_quality_case_status ON quality_case(status);
CREATE INDEX idx_quality_case_owner ON quality_case(owner);
CREATE INDEX idx_quality_case_time ON quality_case(created_at);
CREATE INDEX idx_quality_case_defect ON quality_case(defect_type, abnormal_pattern, risk_level);
CREATE INDEX idx_quality_case_product ON quality_case(work_order, product_name);

CREATE TABLE knowledge_rule (
    rule_id BIGSERIAL PRIMARY KEY,
    defect_type VARCHAR(32) NOT NULL,
    abnormal_pattern VARCHAR(64) NOT NULL,
    risk_level VARCHAR(32),
    cause_priority INT NOT NULL,
    root_cause VARCHAR(128) NOT NULL,
    suggested_action VARCHAR(256) NOT NULL,
    reasoning TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_knowledge_rule_match
ON knowledge_rule(defect_type, abnormal_pattern, risk_level, enabled);

INSERT INTO knowledge_rule
(defect_type, abnormal_pattern, risk_level, cause_priority, root_cause, suggested_action, reasoning)
VALUES
('少锡', '连续3板同点异常', '高', 1, '钢网堵孔', '立即清洗钢网，并检查对应 Pad 开口是否堵塞', '同一 Pad 连续多板少锡，优先怀疑钢网开口堵塞'),
('少锡', '连续3板同点异常', '高', 2, '锡膏变干', '检查锡膏回温、搅拌、使用时间和黏度状态', '连续少锡可能由锡膏流动性下降导致'),
('少锡', '连续3板同点异常', '高', 3, '局部支撑不良', '检查该区域 PCB 支撑和平整度', '局部支撑不足可能导致印刷接触不充分'),
('少锡', '同一元件多Pad异常', '中', 1, 'PCB支撑不良', '检查元件区域支撑和板面平整度', '同一元件多个 Pad 少锡，可能是局部接触不良'),
('少锡', '同一元件多Pad异常', '中', 2, '钢网局部堵塞', '检查该元件对应钢网区域是否堵孔或污染', '多个相邻 Pad 少锡，可能是钢网局部异常'),
('少锡', '整板趋势异常', '中', 1, '刮刀压力过大', '检查并适当降低刮刀压力', '整板偏少可能由刮刀压力过大导致锡膏转移不足'),
('少锡', '整板趋势异常', '高', 1, '刮刀压力过大', '立即检查并调整刮刀压力', '高比例整板少锡，需优先检查印刷参数'),
('多锡', '连续3板同点异常', '高', 1, '钢网底部残锡', '清洗钢网底部，并复测下一块板', '同一 Pad 连续多锡，常见于局部残锡或污染'),
('多锡', '连续3板同点异常', '高', 2, '钢网开口异常', '检查对应 Pad 钢网开口尺寸和状态', '开口偏大或变形可能导致局部锡量过多'),
('多锡', '同一元件多Pad异常', '中', 1, '钢网底部污染', '清洗该元件区域钢网底部', '同一元件多个 Pad 多锡，优先检查局部污染'),
('多锡', '整板趋势异常', '中', 1, '刮刀压力不足', '检查并适当提高刮刀压力', '整板偏多可能由刮刀压力不足导致残留锡膏过多'),
('多锡', '整板趋势异常', '高', 1, 'SPI程序阈值问题', '检查 SPI 程序阈值和标准值设置', '大面积多锡也可能由阈值不合理导致');
