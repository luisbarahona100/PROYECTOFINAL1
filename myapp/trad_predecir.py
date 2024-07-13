import numpy as np
from tensorflow.keras.preprocessing import image

def predict_sign(model, img_path):
    img = image.load_img(img_path, target_size=(64, 64))
    img_array = image.img_to_array(img)
    img_array = np.expand_dims(img_array, axis=0) / 255.0
    
    prediction = model.predict(img_array)
    classes = ['hola', 'adios']
    
    return classes[np.argmax(prediction)]

# Ejemplo de uso
img_path = 'path_to_new_image'
print(predict_sign(model, img_path))
