"""Single source for metadata choices in validation and the interface."""
CATEGORIES = [
    ('1', 'Phim & hoạt hình'), ('2', 'Ô tô & xe cộ'), ('10', 'Âm nhạc'),
    ('15', 'Thú cưng'), ('17', 'Thể thao'), ('19', 'Du lịch'), ('20', 'Trò chơi'),
    ('22', 'Con người & blog'), ('23', 'Hài'), ('24', 'Giải trí'), ('25', 'Tin tức'),
    ('26', 'Hướng dẫn & phong cách'), ('27', 'Giáo dục'),
    ('28', 'Khoa học & công nghệ'), ('29', 'Phi lợi nhuận'),
]
LANGUAGES = [
    ('', 'Không khai báo'), ('vi', 'Tiếng Việt'), ('en', 'Tiếng Anh'),
    ('en-US', 'Tiếng Anh (Mỹ)'), ('ja', 'Tiếng Nhật'), ('ko', 'Tiếng Hàn'),
    ('zh-CN', 'Tiếng Trung'), ('es', 'Tiếng Tây Ban Nha'), ('fr', 'Tiếng Pháp'),
    ('de', 'Tiếng Đức'), ('pt-BR', 'Tiếng Bồ Đào Nha'), ('hi', 'Tiếng Hindi'),
    ('th', 'Tiếng Thái'), ('id', 'Tiếng Indonesia'),
]
CATEGORY_IDS = {value for value, _ in CATEGORIES}
LANGUAGE_IDS = {value for value, _ in LANGUAGES}


def validate_choices(values):
    for key, allowed, label in (('category', CATEGORY_IDS, 'Danh mục'),
                                ('language', LANGUAGE_IDS, 'Ngôn ngữ')):
        if key in values and (not isinstance(values[key], str) or values[key] not in allowed):
            raise ValueError(label + ' không hợp lệ.')
